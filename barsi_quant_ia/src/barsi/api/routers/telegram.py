"""Webhook e comandos interativos do Bot do Telegram.

Suporta upload de extratos OFX/ZIP, comandos operacionais (/status, /radar, /payout,
/anomalias, /fechamento, /backup, /health, /schedule, /notifications, /run)
e envio assíncrono de mensagens e documentos.
"""
import os
import sqlite3
from datetime import datetime
import httpx
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from barsi.config import settings
from barsi.infra.logging import logger
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.rules import atualizar_posicao_quant
from barsi.services.early_warning_service import EarlyWarningService
from barsi.api.common import get_canonical_positions, get_sync_service

router = APIRouter(tags=["telegram"])

# Nota: `_telegram_run_lock` existe em ambos `telegram.py` (canônico do router)
# e `http.py` (espelho legado, acessado por testes automatizados). Mantemos ambos
# sincronizados via _is_tg_locked() / _set_tg_locked() para não quebrar testes que
# manipulam diretamente `http._telegram_run_lock`.
_telegram_run_lock = False


def _is_tg_locked() -> bool:
    try:
        import barsi.api.http as http_mod
        return getattr(http_mod, "_telegram_run_lock", _telegram_run_lock)
    except Exception:
        return _telegram_run_lock


def _set_tg_locked(val: bool):
    global _telegram_run_lock
    _telegram_run_lock = val
    try:
        import barsi.api.http as http_mod
        http_mod._telegram_run_lock = val
    except Exception:
        pass


async def send_telegram_message(bot_token: str, chat_id: str, reply_text: str):
    """Envia mensagem assíncrona de resposta para o Telegram Bot."""
    if not bot_token:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(url, json={
                "chat_id": chat_id,
                "text": reply_text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            })
    except Exception as err:
        logger.error("telegram_reply_failed", error=str(err))


@router.post("/api/telegram/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """Webhook do Telegram Bot para comandos interativos e conciliação de arquivos."""
    expected_secret = (settings.telegram_webhook_secret or "").strip()
    if expected_secret:
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").strip()
        if received_secret != expected_secret:
            logger.warning("telegram_invalid_webhook_secret", ip=request.client.host if request.client else "")
            raise HTTPException(status_code=403, detail="invalid_webhook_secret")

    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid_json"}

    msg = data.get("message") or data.get("edited_message") or {}
    chat_id = str(msg.get("chat", {}).get("id") or "")

    if not chat_id:
        return {"ok": True}

    configured_chat = str(settings.telegram_chat_id or "").strip()
    if not configured_chat or chat_id != configured_chat:
        logger.warning("telegram_unauthorized_chat", chat_id=chat_id)
        return {"ok": True}

    text = (msg.get("text") or "").strip()
    document = msg.get("document")
    repo = BudgetRepository(settings.sqlite_db_path)
    bot_token = settings.telegram_bot_token

    async def _send_reply(reply_text: str):
        try:
            import barsi.api.http as http_mod
            sender = getattr(http_mod, "send_telegram_message", send_telegram_message)
        except Exception:
            sender = send_telegram_message
        await sender(bot_token, chat_id, reply_text)

    # 1. Trata upload de arquivos (.ofx ou .zip) enviados como documento
    if document:
        file_name = (document.get("file_name") or "").lower()
        if not file_name.endswith((".ofx", ".zip")):
            await _send_reply("⚠️ Envie apenas arquivos de extrato `.ofx` ou pacotes `.zip`.")
            return {"ok": True}

        file_id = document.get("file_id")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r1 = await client.get(
                    f"https://api.telegram.org/bot{bot_token}/getFile",
                    params={"file_id": file_id}
                )
                if r1.status_code != 200:
                    await _send_reply(f"❌ Falha ao obter metadados do arquivo (HTTP {r1.status_code}).")
                    return {"ok": True}
                r1_data = r1.json()
                if not r1_data.get("ok"):
                    await _send_reply("❌ Falha ao obter metadados do arquivo na API do Telegram.")
                    return {"ok": True}
                file_path = r1_data["result"]["file_path"]

                r2 = await client.get(
                    f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
                )
                if r2.status_code != 200 or not r2.content:
                    await _send_reply(f"❌ Falha ao baixar arquivo do Telegram (HTTP {r2.status_code}).")
                    return {"ok": True}
                if len(r2.content) < 10 or len(r2.content) > 20 * 1024 * 1024:
                    await _send_reply(f"❌ Tamanho de arquivo inválido ({len(r2.content)} bytes).")
                    return {"ok": True}
                content = r2.content
        except Exception as exc:
            logger.error("telegram_ofx_download_failed", error=str(exc))
            await _send_reply(f"❌ Falha ao baixar extrato: {exc}")
            return {"ok": True}

        # Validação de assinatura OFX (defesa em profundidade)
        if b"<OFX>" not in content and b"OFXHEADER" not in content and not file_name.endswith(".zip"):
            await _send_reply("❌ O arquivo recebido não possui cabeçalho ou assinatura OFX válida.")
            return {"ok": True}

        os.makedirs("extratos", exist_ok=True)
        safe_name = os.path.basename(file_name)
        target = os.path.join("extratos", safe_name)
        with open(target, "wb") as f:
            f.write(content)

        from barsi.services.ofx_service import OFXParserService
        OFXParserService.reload_keywords_from_db()
        resumos = OFXParserService.processar_diretorio_extratos("extratos")
        todas = [t for r in resumos.values() for t in r.transacoes]
        if todas:
            repo.persist_transacoes(todas)
        reconciled = repo.reconcile_ofx_to_positions(resumos)

        # Radar de anomalias (ML-1)
        anomalies = []
        try:
            notifier = AdapterFactory.create_notification_adapter(settings)
            early_warning = EarlyWarningService(repo, notification_adapter=notifier)
            anomalies = await early_warning.scan_ofx_anomalies(todas)
        except Exception as err:
            logger.warning("telegram_ofx_anomalies_scan_error", error=str(err))

        msg_out = (
            f"✅ *Extrato Bancário Processado via Telegram*\n\n"
            f"📄 *Arquivo:* `{safe_name}` ({max(1, len(content) // 1024)} KB)\n"
            f"📅 *Meses:* {', '.join(resumos.keys()) or '—'}\n"
            f"📊 *Transações Persistidas:* {len(todas)}\n"
            f"🔄 *Ativos Conciliados na Carteira:* {reconciled}"
        )
        if anomalies:
            msg_out += f"\n\n🚨 *{len(anomalies)} anomalia(s) detectada(s) (ML-1):*"
            for a in anomalies[:3]:
                desc = a.get("descricao", "Transação")[:28]
                msg_out += f"\n• {desc} — R$ {abs(a.get('valor', 0.0)):,.2f} ({a.get('severity', 'ALTA')})"
        else:
            msg_out += "\n\n🟢 *Nenhuma anomalia detectada* no perfil de gastos."

        await _send_reply(msg_out)
        return {"ok": True}

    if not text:
        return {"ok": True}

    cmd = text.split()[0].lower()
    args = text.split()[1:]

    if cmd in ("/status", "/carteira"):
        positions = await get_canonical_positions()
        patrimonio = sum((p.qtd_antes or 0) * (p.preco_atual or 0.0) for p in positions)
        proventos = sum(((p.qtd_antes or 0) + (p.qtd_aporte or 0)) * (p.div_proj or 0.0) for p in positions)
        acumular = sum(1 for p in positions if p.status_barsi.value == "ACUMULAR")
        resposta = (
            f"💼 *Cockpit Barsi Quant IA*\n\n"
            f"• *Patrimônio:* R$ {patrimonio:,.2f}\n"
            f"• *Ativos Monitorados:* {len(positions)}\n"
            f"• *Em Zona de Aporte:* {acumular}\n"
            f"• *Proventos Proj. Ano:* R$ {proventos:,.2f}"
        )
        await _send_reply(resposta)

    elif cmd == "/radar":
        positions = await get_canonical_positions()
        for p in positions:
            atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
        acumular = [p for p in positions if p.status_barsi.value == "ACUMULAR"]
        acumular.sort(key=lambda x: x.margem_seguranca, reverse=True)
        if not acumular:
            resposta = "🎯 *Radar B.E.S.T.*: Nenhum ativo abaixo do Preço Teto no momento."
        else:
            linhas = [f"• *{p.ticker}*: R$ {p.preco_atual:.2f} (Teto R$ {p.preco_teto:.2f} · Margem: +{p.margem_seguranca:.1f}%)" for p in acumular[:6]]
            resposta = "🎯 *Radar B.E.S.T. (Top Oportunidades)*\n\n" + "\n".join(linhas)
        await _send_reply(resposta)

    elif cmd in ("/radar_b3", "/radar_ampliado"):
        margem_min = 20.0
        if args:
            try:
                margem_min = float(args[0].replace("%", "").replace(",", "."))
            except ValueError:
                margem_min = 20.0

        await _send_reply(f"🔍 Executando screening ampliado da B3 (margem >= {margem_min:.0f}%)...")
        try:
            from barsi.services.radar_screening_service import RadarScreeningService
            market = AdapterFactory.create_market_adapter(settings)
            positions = await get_canonical_positions()
            oportunidades = await RadarScreeningService.screen_opportunities(
                market_adapter=market,
                margem_min=margem_min,
                canonical_positions=positions,
            )
            msg_radar = RadarScreeningService.format_telegram_summary(oportunidades, margem_min=margem_min)
            await _send_reply(msg_radar)
        except Exception as e:
            logger.error("telegram_radar_b3_failed", error=str(e))
            await _send_reply(f"❌ Erro ao executar radar B3: {e}")

    elif cmd in ("/setores", "/rebalanceamento", "/concentracao"):
        try:
            from barsi.services.portfolio_balance_service import PortfolioBalanceService
            positions = await get_canonical_positions()
            analise = PortfolioBalanceService.analyze_sector_allocation(positions)
            msg_setores = PortfolioBalanceService.format_telegram_report(analise)
            await _send_reply(msg_setores)
        except Exception as e:
            logger.error("telegram_sectors_failed", error=str(e))
            await _send_reply(f"❌ Erro ao analisar setores: {e}")

    elif cmd == "/proventos":
        total_hist = repo.get_total_dividends_received()
        ano_atual = datetime.now().year
        total_ano = repo.get_total_dividends_received(year=ano_atual)
        resposta = (
            f"💵 *Histórico de Proventos Reais Recebidos*\n\n"
            f"• *Total em {ano_atual}:* R$ {total_ano:,.2f}\n"
            f"• *Acumulado Histórico:* R$ {total_hist:,.2f}"
        )
        await _send_reply(resposta)

    elif cmd == "/ticker":
        if not args:
            await _send_reply("⚠️ Uso correto: `/ticker BBAS3`")
        else:
            t_alvo = args[0].strip().upper()
            positions = await get_canonical_positions()
            p = next((x for x in positions if x.ticker == t_alvo), None)
            if not p:
                await _send_reply(f"❌ Ativo *{t_alvo}* não encontrado na carteira.")
            else:
                atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
                resposta = (
                    f"📊 *Análise Barsi: {p.ticker}*\n\n"
                    f"• *Empresa:* {p.empresa or p.setor}\n"
                    f"• *Cotação Atual:* R$ {p.preco_atual:.2f}\n"
                    f"• *Preço Teto:* R$ {p.preco_teto:.2f}\n"
                    f"• *Margem de Segurança:* {p.margem_seguranca:.1f}%\n"
                    f"• *DPA Projetado:* R$ {p.div_proj:.2f}\n"
                    f"• *Decisão Barsi:* `{p.status_barsi.value}`"
                )
                await _send_reply(resposta)

    elif cmd == "/run":
        if _is_tg_locked():
            await _send_reply("⏳ Já existe uma análise quantitativa em andamento na TV Box. Por favor, aguarde.")
            return {"ok": True}

        _set_tg_locked(True)
        await _send_reply("🚀 Ciclo de análise quantitativa disparado em segundo plano...")
        async def _run_task():
            try:
                summary = await get_sync_service().execute_run()
                acumular_cnt = summary.actions_recommended.get("ACUMULAR", 0) if getattr(summary, "actions_recommended", None) else 0
                await _send_reply(
                    f"✅ *Ciclo Concluído com Sucesso!*\n\n"
                    f"• Ativos analisados: {summary.tickers_analyzed}\n"
                    f"• Em zona de aporte: {acumular_cnt}\n"
                    f"• Duração: {summary.duration_ms}ms"
                )
            except Exception as e:
                logger.error("telegram_run_task_failed", error=str(e))
                await _send_reply(f"❌ Falha durante execução do ciclo: {str(e)}")
            finally:
                _set_tg_locked(False)

        background_tasks.add_task(_run_task)

    elif cmd == "/schedule":
        cfg = repo.get_scheduler_config()
        if not args:
            next_run = repo.next_scheduled_run() or "—"
            await _send_reply(
                f"⏰ *Scheduler Barsi*\n\n"
                f"• Status: {'🟢 ON' if cfg['enabled'] else '🔴 OFF'}\n"
                f"• Dias (ISO): `{cfg['days_of_week']}`\n"
                f"• Horas: `{cfg['hours']}`\n"
                f"• Minuto: `{cfg['minute']}`\n"
                f"• Próximo: `{next_run}`\n\n"
                f"Uso:\n"
                f"`/schedule on|off`\n"
                f"`/schedule days 1,2,3,4,5`\n"
                f"`/schedule hours 9,10,11,12,13,14,15,16,17,18`\n"
                f"`/schedule minute 30`"
            )
        elif len(args) == 1 and args[0] in ("on", "off"):
            repo.update_scheduler_config(enabled=(args[0] == "on"), updated_by=f"telegram:{chat_id}")
            await _send_reply(f"✅ Scheduler {'ativado' if args[0] == 'on' else 'desativado'}.")
        elif len(args) >= 2:
            key, val = args[0], " ".join(args[1:])
            try:
                if key == "days":
                    repo.update_scheduler_config(days_of_week=val, updated_by=f"telegram:{chat_id}")
                elif key == "hours":
                    repo.update_scheduler_config(hours=val, updated_by=f"telegram:{chat_id}")
                elif key == "minute":
                    repo.update_scheduler_config(minute=int(val), updated_by=f"telegram:{chat_id}")
                else:
                    await _send_reply("⚠️ Chaves válidas: days | hours | minute")
                    return {"ok": True}
                await _send_reply(f"✅ `{key}` = `{val}` gravado.")
            except Exception as e:
                await _send_reply(f"⚠️ Erro: {e}")
        else:
            await _send_reply("⚠️ Uso: `/schedule`, `/schedule on|off`, `/schedule days|hours|minute <valor>`")

    elif cmd == "/notifications":
        if not args:
            prefs = repo.list_notification_preferences()
            linhas = [
                f"• `{p['alert_type']}`: {'🟢 ON' if p['enabled'] else '🔴 OFF'}"
                for p in prefs
            ] or ["_(nenhuma preferência configurada — todas ON por padrão)_"]
            await _send_reply(
                "🔔 *Preferências de Notificação*\n\n" + "\n".join(linhas) +
                "\n\nUso:\n"
                "`/notifications on` ou `/notifications off`\n"
                "`/notifications margem_20 off`"
            )
        elif len(args) == 1 and args[0] in ("on", "off"):
            enabled = args[0] == "on"
            count = repo.set_all_notifications_enabled(enabled, updated_by=f"telegram:{chat_id}")
            await _send_reply(f"✅ Todas as notificações {'ativadas' if enabled else 'desativadas'} ({count} tipos).")
        elif len(args) == 2 and args[1] in ("on", "off"):
            alert_type, state = args[0], args[1]
            enabled = state == "on"
            try:
                repo.set_notification_enabled(alert_type, enabled, updated_by=f"telegram:{chat_id}")
                await _send_reply(f"✅ `{alert_type}` agora está {'🟢 ON' if enabled else '🔴 OFF'}.")
            except ValueError as e:
                await _send_reply(f"⚠️ {e}")
        else:
            await _send_reply("⚠️ Uso: `/notifications`, `/notifications on|off`, `/notifications <tipo> on|off`")

    elif cmd == "/anomalias":
        # Consulta anomalias registradas recentemente (com ou sem ticker associado)
        all_alerts = repo.list_thesis_alerts(active_only=True)
        alerts = [a for a in all_alerts if a.get("risk_type") == "ANOMALIA_EXTRATO"]
        if not alerts:
            await _send_reply("✅ *Radar de Anomalias (ML-1)*\n\nNenhuma anomalia financeira ativa detectada no momento.")
        else:
            linhas = []
            for a in alerts[:6]:
                ticker_tag = f"*{a['ticker']}*: " if a.get("ticker") and a["ticker"] != "EXTRATO" else ""
                linhas.append(f"• {ticker_tag}{a['headline']} ({a['severity']})")
            await _send_reply("🚨 *Anomalias Ativas no Radar (ML-1)*\n\n" + "\n".join(linhas))

    elif cmd == "/payout":
        if not args:
            await _send_reply("⚠️ Uso correto: `/payout BBAS3`")
        else:
            t_alvo = args[0].strip().upper()
            positions = await get_canonical_positions()
            p = next((x for x in positions if x.ticker == t_alvo), None)
            if not p:
                await _send_reply(f"❌ Ativo *{t_alvo}* não encontrado na carteira.")
            else:
                from barsi.domain.payout_scorer import PayoutScorer
                from barsi.domain.quant_ai import StatisticalBarsiEngine
                atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
                setor_classificado = StatisticalBarsiEngine.identificar_setor(p.ticker)
                perenidade = StatisticalBarsiEngine.SETOR_STATS.get(
                    setor_classificado, StatisticalBarsiEngine.SETOR_STATS.get("OUTROS", {})
                ).get("perenidade", 0.85)
                scorer = PayoutScorer.get_instance()
                res = scorer.score(p, extras={"perenidade": perenidade})

                contribs = sorted(res["contributions"].items(), key=lambda x: x[1], reverse=True)
                top_pos = [f"  + `{k}`: +{v:.2f}" for k, v in contribs if v > 0][:3]
                top_neg_raw = sorted([(k, v) for k, v in contribs if v < 0], key=lambda x: x[1])[:2]
                top_neg = [f"  - `{k}`: {v:.2f}" for k, v in top_neg_raw]
                detalhes = "\n".join(top_pos + top_neg) or "  • Contribuições neutras"

                resp = (
                    f"📊 *Sustentabilidade de Proventos v2 (ML-2)*\n"
                    f"• Ativo: *{p.ticker}* ({p.empresa or setor_classificado})\n"
                    f"• Escore: *{res['score']:.1%}* {res['badge']} ({res['label']})\n"
                    f"• Logit: `{res['logit']:.2f}` (Base: `{res['intercept']:.2f}`)\n\n"
                    f"*Fatores Principais:*\n{detalhes}"
                )
                await _send_reply(resp)

    elif cmd == "/fechamento":
        mes_alvo = args[0].strip() if args else None
        await _send_reply("⏳ Gerando relatório executivo de fechamento mensal...")
        try:
            from barsi.services.monthly_closing_service import MonthlyClosingService
            notifier = AdapterFactory.create_notification_adapter(settings)
            closing_svc = MonthlyClosingService(repo)
            ok = await closing_svc.send_closing_via_telegram(notifier, mes_ano=mes_alvo)
            if not ok:
                await _send_reply("⚠️ Não foi possível compilar o documento de fechamento mensal.")
        except Exception as e:
            logger.error("telegram_closing_command_failed", error=str(e))
            await _send_reply(f"❌ Erro ao gerar fechamento mensal: {e}")

    elif cmd == "/backup":
        db_path = settings.sqlite_db_path
        if not os.path.exists(db_path):
            await _send_reply("❌ Banco de dados SQLite não encontrado no servidor.")
        else:
            await _send_reply("📦 Preparando backup offsite do banco SQLite canônico...")
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                    tmp_backup_path = tmp.name

                try:
                    src_conn = sqlite3.connect(db_path)
                    dst_conn = sqlite3.connect(tmp_backup_path)
                    try:
                        src_conn.backup(dst_conn)
                    finally:
                        src_conn.close()
                        dst_conn.close()

                    with open(tmp_backup_path, "rb") as f:
                        db_bytes = f.read()
                finally:
                    if os.path.exists(tmp_backup_path):
                        os.remove(tmp_backup_path)

                now_str = datetime.now().strftime("%Y-%m-%d_%H%M")
                filename = f"barsi_memory_backup_{now_str}.db"
                size_kb = max(1, len(db_bytes) // 1024)
                caption = (
                    f"🗄️ *Backup Canônico SQLite Barsi Quant IA*\n\n"
                    f"• Arquivo: `{filename}`\n"
                    f"• Tamanho: `{size_kb}` KB\n"
                    f"• Data: `{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}`\n"
                    f"• Banco: `{os.path.basename(db_path)}`\n\n"
                    f"🔒 _Guarde este arquivo como cópia de segurança offsite._"
                )
                notifier = AdapterFactory.create_notification_adapter(settings)
                if hasattr(notifier, "send_document"):
                    sent = await notifier.send_document(
                        filename=filename,
                        content=db_bytes,
                        caption=caption,
                        content_type="application/vnd.sqlite3"
                    )
                    if not sent:
                        await _send_reply("⚠️ Falha ao transmitir backup como documento para o Telegram.")
                else:
                    await _send_reply("⚠️ Notificador configurado não suporta envio de documentos binários.")
            except Exception as exc:
                logger.error("telegram_backup_failed", error=str(exc))
                await _send_reply(f"❌ Erro ao enviar backup: {exc}")

    elif cmd == "/health":
        from barsi.domain.anomaly_detector import AnomalyDetector
        from barsi.domain.payout_scorer import PayoutScorer

        det = AnomalyDetector.get_instance()
        payout = PayoutScorer.get_instance()
        positions = await get_canonical_positions()

        ml_status = (
            f"• *Isolation Forest (ML-1):* {'🟢 Carregado' if det.is_loaded else '🟡 Fallback (Heurístico)'} (Threshold: `{det.anomaly_threshold}`)\n"
            f"• *Payout Scorer (ML-2):* {'🟢 Carregado' if payout.is_loaded else '🟡 Baseline Barsi'} (Pesos: `{os.path.basename(payout.weights_path) if payout.weights_path else 'default'}`)\n"
            f"• *Ativos Monitorados:* `{len(positions)}`\n"
            f"• *SQLite Canônico:* `{os.path.basename(settings.sqlite_db_path)}` (Integridade: `OK`)\n"
            f"• *Ambiente:* `TV Box / Edge Runtime`\n"
            f"• *Data/Hora Servidor:* `{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}`"
        )
        await _send_reply("🏥 *Diagnóstico de Saúde & Status Operacional*\n\n" + ml_status)

    elif cmd in ("/wrapped", "/retrospectiva", "/wrapped_img"):
        ano_alvo = None
        quer_imagem = cmd == "/wrapped_img"
        for arg in args:
            arg_clean = arg.strip().lower()
            if arg_clean in ("img", "imagem", "foto", "stories"):
                quer_imagem = True
            elif arg_clean.isdigit():
                try:
                    ano_alvo = int(arg_clean)
                except ValueError:
                    pass

        if not ano_alvo:
            now = datetime.now()
            ano_alvo = now.year - 1 if now.month == 1 else now.year

        await _send_reply(f"🎁 Compilando *Barsi Wrapped {ano_alvo}*...")
        try:
            from barsi.services.wrapped_service import WrappedService
            from barsi.services.wrapped_image_service import WrappedImageService
            positions = await get_canonical_positions()
            wrapped_data = WrappedService.get_annual_retrospective(ano_alvo, repo, positions)
            card = WrappedService.generate_telegram_card(wrapped_data)
            await _send_reply(card)

            # Geração do Card PNG Stories / WhatsApp
            img_bytes = WrappedImageService.generate_image_bytes(wrapped_data)
            if img_bytes:
                notifier = AdapterFactory.create_notification_adapter(settings)
                if hasattr(notifier, "send_photo"):
                    await notifier.send_photo(
                        content=img_bytes,
                        filename=f"barsi_wrapped_{ano_alvo}.png",
                        caption=f"📸 *Barsi Wrapped {ano_alvo}* — Seu card de dividendos pronto para compartilhar!"
                    )
                elif hasattr(notifier, "send_document"):
                    await notifier.send_document(
                        filename=f"barsi_wrapped_{ano_alvo}.png",
                        content=img_bytes,
                        caption=f"📸 *Barsi Wrapped {ano_alvo}*",
                        content_type="image/png"
                    )
        except Exception as e:
            logger.error("telegram_wrapped_command_failed", error=str(e))
            await _send_reply(f"❌ Erro ao compilar Barsi Wrapped: {e}")

    else:
        ajuda = (
            "🤖 *Comandos do Barsi Quant Bot:*\n\n"
            "• `/status` — Visão geral da carteira e patrimônio\n"
            "• `/radar` — Ativos da carteira com maior margem de segurança\n"
            "• `/radar_b3 [MARGEM]` — Screening de 28 ativos B.E.S.T. fora da carteira\n"
            "• `/setores` — Rebalanceamento e alerta de concentração B.E.S.T.\n"
            "• `/wrapped [ANO]` — Retrospectiva anual (Texto + Card PNG Stories)\n"
            "• `/payout BBAS3` — Sustentabilidade de dividendos v2 (ML-2)\n"
            "• `/anomalias` — Radar de anomalias em extratos (ML-1)\n"
            "• `/fechamento [YYYY-MM]` — Gerar e enviar PDF do fechamento mensal\n"
            "• `/backup` — Baixar snapshot offsite do SQLite canônico\n"
            "• `/health` — Diagnóstico de saúde dos modelos de ML e TV Box\n"
            "• `/proventos` — Total de dividendos recebidos\n"
            "• `/ticker BBAS3` — Detalhes completos de um ativo\n"
            "• `/schedule` — Configurar agendamento automático\n"
            "• `/notifications` — Gerenciar preferências de alerta\n"
            "• `/run` — Disparar ciclo quantitativo B3 agora\n"
            "• 📎 *Upload de OFX*: Envie um arquivo `.ofx` ou `.zip` diretamente para conciliação\n"
            "• `/help` — Exibir este menu de ajuda"
        )
        await _send_reply(ajuda)

    return {"ok": True}
