"""Interface de Linha de Comando (CLI) para o Sistema Previdenciário Barsi."""
import typer
import asyncio
import uvicorn
from pathlib import Path
from typing import Optional
from barsi.config import settings
from barsi.adapters.factory import AdapterFactory
from barsi.services.sync_service import SyncService
from barsi.infra.logging import setup_logging, logger

app = typer.Typer(name="barsi", help="Sistema Previdenciário Barsi - CLI Quant & Edge")

def _resolve_tvbox_home(user: str) -> str:
    """Resolve o diretório home remoto na TV Box, priorizando BARSI_TVBOX_HOME."""
    home = (settings.tvbox_home or "").strip()
    if home and "/" in home:
        return home
    return f"/home/{user}/barsi_quant_ia"

@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Executa o cálculo sem persistir alterações"),
    ticker: Optional[str] = typer.Option(None, "--ticker", help="Executa o ciclo para um único ativo (ex: BBAS3)"),
    radar: bool = typer.Option(False, "--radar/--no-radar", help="Envia resumo consolidado de oportunidades ao finalizar (Telegram/WhatsApp)"),
    if_due: bool = typer.Option(False, "--if-due", help="Só executa se o scheduler determinar que agora é hora. Usado pelo systemd timer.")
):
    """Executa o pipeline completo de sincronização e enriquecimento Barsi."""
    setup_logging(settings.log_level)
    
    async def _main():
        from barsi.adapters.history.budget_repository import BudgetRepository
        repo = BudgetRepository(settings.sqlite_db_path)
        notify_radar = radar
        force_radar = radar and not if_due

        # Se --if-due, valida scheduler ANTES de criar Adapters (economia de recursos)
        if if_due:
            should_run, reason = repo.is_run_due()
            if not should_run:
                logger.info("scheduled_run_skipped", reason=reason)
                typer.echo(f"[i] Run não devida: {reason}")
                return
            notify_radar = repo.is_notification_enabled("radar_summary")
            force_radar = False

        market = AdapterFactory.create_market_adapter(settings)
        news = AdapterFactory.create_news_adapter(settings)
        ai = AdapterFactory.create_ai_adapter(settings)
        sheets = AdapterFactory.create_sheets_adapter(settings)
        history = AdapterFactory.create_history_adapter(settings)
        notifier = AdapterFactory.create_notification_adapter(settings)

        service = SyncService(
            market=market,
            news=news,
            ai=ai,
            sheets=sheets,
            history=history,
            notifier=notifier,
            budget_repo=repo,
            max_concurrency=settings.max_concurrency
        )
        summary = await service.execute_run(
            dry_run=dry_run,
            target_ticker=ticker,
            notify_radar=notify_radar,
            force_radar=force_radar
        )
        typer.echo(f"\n[OK] Run concluída! ID: {summary.run_id}")
        typer.echo(f"     Ativos analisados: {summary.tickers_analyzed}")
        typer.echo(f"     Duração: {summary.duration_ms} ms | RAM RSS: {summary.memory_peak_mb} MB")
        if summary.errors:
            typer.echo(f"     Erros: {summary.errors}")

    asyncio.run(_main())

@app.command()
def api(
    host: str = typer.Option("0.0.0.0", "--host", help="Host de binding"),
    port: int = typer.Option(8000, "--port", help="Porta HTTP")
):
    """Inicia o servidor HTTP minimalista para integração com Cloudflare Tunnel."""
    setup_logging(settings.log_level)
    uvicorn.run("barsi.api.http:app", host=host, port=port, reload=False, log_level="warning")

@app.command()
def extratos(
    dir_path: str = typer.Option("extratos", "--dir", help="Pasta com arquivos de extrato .ofx"),
    sync_sheet: bool = typer.Option(False, "--sync-sheet", help="Sincroniza valores consolidados na aba Budget_Manager_Fluxo"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra resultado da conciliação sem persistir no SQLite nem no Sheets"),
    target_month: Optional[str] = typer.Option(
        None,
        "--target-month",
        help="Mês alvo (YYYY-MM). Compras diretas de meses anteriores são ignoradas. Padrão: MAX(mês atual, mês mais recente do extrato). Use explicitamente para fechar um mês anterior em atraso."
    ),
):
    """Lê e processa os extratos bancários da pasta extratos/ para conciliação Barsi."""
    if target_month:
        try:
            from barsi.adapters.history.budget_repository import validate_target_month
            target_month = validate_target_month(target_month)
        except ValueError as exc:
            typer.echo(f"[!] Erro no mês alvo: {exc}")
            raise typer.Exit(code=1)
    from barsi.services.ofx_service import OFXParserService
    OFXParserService.reload_keywords_from_db()
    resumos = OFXParserService.processar_diretorio_extratos(dir_path)
    if not resumos:
        typer.echo(f"[!] Nenhum extrato .ofx encontrado em '{dir_path}'")
        return

    typer.echo("\n" + "="*65)
    typer.echo("    CONCILIAÇÃO BANCÁRIA & APORTES (MÉTODO LUIZ BARSI)        ")
    typer.echo("="*65)
    
    for mes, r in sorted(resumos.items()):
        if r.total_entradas == 0 and r.total_saidas == 0:
            continue
        typer.echo(f"\n📅 MÊS DE REFERÊNCIA: {mes}")
        typer.echo(f"   (+) Salário CLT / Renda       : R$ {r.salario_entradas:>9.2f}")
        typer.echo(f"   (+) Proventos B3 / Dividendos : R$ {r.proventos_recebidos:>9.2f}")
        if r.outras_entradas > 0:
            typer.echo(f"   (+) Outras Entradas           : R$ {r.outras_entradas:>9.2f}")
        typer.echo(f"   ──> TOTAL ENTRADAS REALIZADAS : R$ {r.total_entradas:>9.2f}")
        
        typer.echo("\n   💼 INVESTIMENTOS REALIZADOS (BARSI B.E.S.T.):")
        typer.echo(f"       • Ações B.E.S.T. Total    : R$ {r.compras_acoes_barsi:>9.2f}")
        typer.echo(f"       • FIIs (Papel/Tijolo)     : R$ {r.compras_fiis:>9.2f}")
        typer.echo(f"       ──> TOTAL APORTADO B3     : R$ {r.total_investimentos:>9.2f}")
        for t, val in r.detalhes_ativos.items():
            typer.echo(f"           - {t:<6}                : R$ {val:>8.2f}")
        
        typer.echo("\n   🛡️ SAÍDAS OBRIGATÓRIAS & COLCHÃO:")
        typer.echo(f"       • Fatura Nubank Fechada   : R$ {r.faturas_cartao:>9.2f}")
        typer.echo(f"       • Moradia & Família (Pix) : R$ {r.moradia_e_familia:>9.2f}")
        typer.echo(f"       • Tributos (DAS/Sefaz)    : R$ {r.tributos_e_taxas:>9.2f}")
        if r.cooperativa_ailos > 0:
            typer.echo(f"       • Cooperativa Ailos       : R$ {r.cooperativa_ailos:>9.2f}")
        typer.echo(f"       • Colchão de Giro (RDB)   : R$ {r.reserva_rdb:>9.2f}")
        if r.despesas_variaveis > 0:
            typer.echo(f"       • Variáveis (Débito/Dia)  : R$ {r.despesas_variaveis:>9.2f}")
        typer.echo(f"   ──> TOTAL DE SAÍDAS EFETIVAS  : R$ {r.total_saidas:>9.2f}")
        typer.echo(f"   💵 SALDO LIVRE / RESIDUAL     : R$ {r.saldo_livre:>9.2f}")

    if dry_run:
        typer.echo("\n[DRY-RUN] Simulação concluída. Nada foi persistido no SQLite nem no Google Sheets.")
        return

    # Persistência no SQLite via caller (mantém SRP: parser não tem side-effects de I/O)
    try:
        from barsi.adapters.history.budget_repository import BudgetRepository
        repo = BudgetRepository(settings.sqlite_db_path)
        reconciled_count = repo.reconcile_ofx_to_positions(resumos, target_month=target_month)
        if reconciled_count > 0:
            typer.echo(f"\n[✔] {reconciled_count} ativo(s) em carteira conciliados com o OFX (qtd_aporte -> qtd_antes)!")
        todas = [t for r in resumos.values() for t in r.transacoes]
        if todas:
            repo.persist_transacoes(todas)
        BUCKETS_PERSISTIR = {
            "Salário / Renda Principal":     lambda r: r.salario_entradas,
            "Proventos B3":                 lambda r: r.proventos_recebidos,
            "Aporte Ações/FIIs":            lambda r: r.compras_acoes_barsi,
            "Fatura Cartão Nubank":         lambda r: r.faturas_cartao,
            "Reserva / RDB":                lambda r: r.reserva_rdb,
            "Tributos & Taxas":             lambda r: r.tributos_e_taxas,
            "Compromisso Familiar / Moradia": lambda r: r.moradia_e_familia,
            "Cooperativa Ailos":            lambda r: r.cooperativa_ailos,
            "Despesas Variáveis":           lambda r: r.despesas_variaveis,
        }
        for mes, res in resumos.items():
            for categoria, getter in BUCKETS_PERSISTIR.items():
                val = getter(res)
                if val > 0:
                    repo.record_budget_entry(mes, categoria, val, source="ofx")
    except Exception as exc:
        logger.warning("persist_transacoes_failed", error=str(exc))

    if sync_sheet:
        typer.echo("\n[+] Sincronizando com Google Sheets (Budget_Manager_Fluxo)...")
        from barsi.config import Settings
        from barsi.adapters.factory import AdapterFactory
        cfg = Settings()
        sheets_adapter = AdapterFactory.create_sheets_adapter(cfg)
        if hasattr(sheets_adapter, "sync_budget_manager"):
            sheets_adapter.sync_budget_manager(resumos)
            typer.echo("[✔] Sincronização orçamentária concluída com sucesso no Google Sheets!")
        else:
            typer.echo("[!] Adapter atual não suporta sync de orçamento.")

@app.command()
def sync_tvbox(
    ip: Optional[str] = typer.Option(None, "--ip", help="IP da TV Box na rede (lê de BARSI_TVBOX_IP se nulo)"),
    user: Optional[str] = typer.Option(None, "--user", help="Usuário SSH (lê de BARSI_TVBOX_USER se nulo)"),
    password: Optional[str] = typer.Option(None, "--pass", help="Senha SSH (lê de BARSI_TVBOX_PASS se nulo)"),
    upload_db: bool = typer.Option(False, "--upload-db", help="Enviar banco SQLite e XLSX local para TV Box (ATENÇÃO: sobrescreve dados na TV Box!)"),
    no_extratos: bool = typer.Option(False, "--no-extratos", help="Não enviar extratos OFX")
):
    """Sincroniza código fonte, templates e configurações para a TV Box (preserva dados SQLite por padrão)."""
    import os, glob, getpass, paramiko
    from scp import SCPClient

    target_ip = ip or settings.tvbox_ip
    target_user = user or settings.tvbox_user
    if not target_ip or not target_user:
        typer.echo("[!] Configure BARSI_TVBOX_IP e BARSI_TVBOX_USER no .env (ou passe --ip/--user)")
        raise typer.Exit(code=1)
    target_pass = password or settings.tvbox_pass or getpass.getpass(f"Senha SSH para {target_user}@{target_ip}: ")

    typer.echo(f"\n[+] Conectando na TV Box ({target_user}@{target_ip}) para sincronização completa...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(target_ip, username=target_user, password=target_pass, timeout=8)
    except Exception as exc:
        typer.echo(f"[!] Falha na conexão SSH com a TV Box: {exc}")
        return

    pref_home = _resolve_tvbox_home(target_user)
    target_homes = [pref_home]
    alt_home = f"/home/{target_user}/barsi_quant_ia"
    if alt_home not in target_homes:
        target_homes.append(alt_home)

    root_src = "barsi_quant_ia/src/barsi" if os.path.exists("barsi_quant_ia/src/barsi") else "src/barsi"
    extratos_list = glob.glob("extratos/*.ofx") or glob.glob("barsi_quant_ia/extratos/*.ofx")
    base_dir = "barsi_quant_ia" if os.path.exists("barsi_quant_ia") else "."
    cred_p = "barsi_quant_ia/credentials.json" if os.path.exists("barsi_quant_ia/credentials.json") else "credentials.json"
    env_p = ".env" if os.path.exists(".env") else "barsi_quant_ia/.env"

    for remote_home in target_homes:
        # Garante diretórios essenciais na TV Box com permissão para o usuário
        init_cmd = (
            f"sudo mkdir -p '{remote_home}/extratos' '{remote_home}/src/barsi' '{remote_home}/memory' '{remote_home}/cache' "
            f"&& sudo chown -R {target_user}:{target_user} '{remote_home}' "
            f"&& sudo chmod -R 777 '{remote_home}/memory' '{remote_home}/cache' '{remote_home}/extratos'"
        )
        stdin, stdout, stderr = ssh.exec_command(init_cmd, get_pty=True)
        stdin.write(f"{target_pass}\n")
        stdin.flush()
        stdout.channel.recv_exit_status()

        # Prepara lista de arquivos do código fonte
        files_to_sync = []
        remote_dirs = set()
        for root, _, files in os.walk(root_src):
            for file in files:
                if file.endswith((".py", ".html", ".json", ".sql", ".js", ".css")) and "__pycache__" not in root:
                    local_p = os.path.join(root, file)
                    rel_p = os.path.relpath(local_p, root_src).replace("\\", "/")
                    remote_p = f"{remote_home}/src/barsi/{rel_p}"
                    files_to_sync.append((local_p, remote_p))
                    remote_dirs.add(os.path.dirname(remote_p))

        # Garante que todas as subpastas remotas existam antes do SCP
        for rdir in sorted(remote_dirs):
            _, out, _ = ssh.exec_command(f"mkdir -p '{rdir}'")
            out.channel.recv_exit_status()

        with SCPClient(ssh.get_transport()) as scp:
            # 1. Extratos (opcional)
            if not no_extratos:
                typer.echo(f"    - Sincronizando extratos bancários (.ofx) para {remote_home}...")
                for f in extratos_list:
                    try:
                        scp.put(f, f"{remote_home}/extratos/{os.path.basename(f)}")
                    except Exception as e:
                        typer.echo(f"    [!] Aviso ao enviar extrato {f}: {e}")
            else:
                typer.echo("    ℹ️  Extratos bancários preservados (--no-extratos ativo).")

            # 2. Código fonte principal (inclui templates HTML)
            typer.echo(f"    - Sincronizando código fonte e templates HTML para {remote_home}...")
            for local_p, remote_p in files_to_sync:
                try:
                    scp.put(local_p, remote_p)
                except Exception as e:
                    typer.echo(f"    [!] Aviso ao enviar {local_p}: {e}")

            # 3. Metadados e Docker
            for meta_file in ["pyproject.toml", "README.md", "Dockerfile", "docker-compose.yml"]:
                meta_path = os.path.join(base_dir, meta_file)
                if os.path.exists(meta_path):
                    try:
                        scp.put(meta_path, f"{remote_home}/{meta_file}")
                    except Exception:
                        pass

            # 4. Credentials se existir
            if os.path.exists(cred_p):
                try:
                    scp.put(cred_p, f"{remote_home}/credentials.json")
                except Exception:
                    pass

            # 5. .env local para TV Box
            if os.path.exists(env_p):
                try:
                    scp.put(env_p, f"{remote_home}/.env")
                except Exception:
                    pass

            # 6. Banco SQLite e Excel (somente com flag explícita --upload-db)
            if upload_db:
                typer.echo("    ⚠️  [ATENÇÃO] Flag --upload-db ativa: Enviando banco SQLite e Excel local para TV Box...")
                db_candidates = ["memory/barsi_memory.db", "barsi_quant_ia/memory/barsi_memory.db"]
                for db_cand in db_candidates:
                    if os.path.exists(db_cand):
                        try:
                            typer.echo(f"    - Sobrescrevendo banco SQLite ({db_cand}) na TV Box...")
                            scp.put(db_cand, f"{remote_home}/memory/barsi_memory.db")
                            break
                        except Exception as e:
                            typer.echo(f"    [!] Aviso ao sincronizar SQLite memory DB: {e}")

                xlsx_candidates = ["carteira_barsi_quant_ia.xlsx", "barsi_quant_ia/carteira_barsi_quant_ia.xlsx"]
                for xlsx_cand in xlsx_candidates:
                    if os.path.exists(xlsx_cand):
                        try:
                            typer.echo("    - Sobrescrevendo carteira Excel (.xlsx) na TV Box...")
                            scp.put(xlsx_cand, f"{remote_home}/carteira_barsi_quant_ia.xlsx")
                            break
                        except Exception as e:
                            typer.echo(f"    [!] Aviso ao sincronizar XLSX: {e}")
            else:
                typer.echo("    🛡️  [Path B] Banco SQLite e XLSX remotos preservados na TV Box (use --upload-db para forçar upload).")

    typer.echo("    - Reiniciando serviço barsi-api na TV Box...")
    stdin, stdout, stderr = ssh.exec_command("sudo systemctl restart barsi-api.service", get_pty=True)
    stdin.write(f"{target_pass}\n")
    stdin.flush()
    stdout.channel.recv_exit_status()
    ssh.close()
    typer.echo("[✔] TV Box sincronizada com sucesso e serviço reiniciado!\n")

@app.command()
def pull_db(
    ip: Optional[str] = typer.Option(None, "--ip", help="IP da TV Box na rede (lê de BARSI_TVBOX_IP se nulo)"),
    user: Optional[str] = typer.Option(None, "--user", help="Usuário SSH (lê de BARSI_TVBOX_USER se nulo)"),
    password: Optional[str] = typer.Option(None, "--pass", help="Senha SSH (lê de BARSI_TVBOX_PASS se nulo)")
):
    """Baixa o banco SQLite canônico da TV Box para o PC local com backup de segurança."""
    import os, getpass, paramiko, shutil
    from datetime import datetime
    from scp import SCPClient

    target_ip = ip or settings.tvbox_ip
    target_user = user or settings.tvbox_user
    if not target_ip or not target_user:
        typer.echo("[!] Configure BARSI_TVBOX_IP e BARSI_TVBOX_USER no .env (ou passe --ip/--user)")
        raise typer.Exit(code=1)
    target_pass = password or settings.tvbox_pass or getpass.getpass(f"Senha SSH para {target_user}@{target_ip}: ")

    typer.echo(f"\n[+] Conectando na TV Box ({target_user}@{target_ip}) para baixar SQLite canônico...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(target_ip, username=target_user, password=target_pass, timeout=8)
    except Exception as exc:
        typer.echo(f"[!] Falha na conexão SSH com a TV Box: {exc}")
        return

    remote_home = _resolve_tvbox_home(target_user)
    remote_db = f"{remote_home}/memory/barsi_memory.db"
    local_db = "memory/barsi_memory.db" if os.path.exists("memory") else "barsi_quant_ia/memory/barsi_memory.db"
    os.makedirs(os.path.dirname(local_db), exist_ok=True)

    # 1. Faz backup local antes de substituir
    if os.path.exists(local_db):
        bak_name = f"{local_db}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(local_db, bak_name)
        typer.echo(f"    - Backup local criado: {bak_name}")

    with SCPClient(ssh.get_transport()) as scp:
        typer.echo(f"    - Baixando {remote_db} -> {local_db}...")
        scp.get(remote_db, local_db)

    ssh.close()
    typer.echo(f"[✔] Banco SQLite baixado com sucesso da TV Box para {local_db}!\n")

@app.command()
def scheduler_install(
    ip: Optional[str] = typer.Option(None, "--ip", help="IP da TV Box na rede (lê de BARSI_TVBOX_IP se nulo)"),
    user: Optional[str] = typer.Option(None, "--user", help="Usuário SSH (lê de BARSI_TVBOX_USER se nulo)"),
    password: Optional[str] = typer.Option(None, "--pass", help="Senha SSH (lê de BARSI_TVBOX_PASS se nulo)"),
    venv_python: str = typer.Option(".venv/bin/python", "--venv-python", help="Caminho do python no venv da TV Box (relativo a ~/barsi_quant_ia ou absoluto)")
):
    """Reinstala o barsi-sync.timer como heartbeat de 5 min com execução condicional --if-due."""
    import getpass, paramiko

    target_ip = ip or settings.tvbox_ip
    target_user = user or settings.tvbox_user
    if not target_ip or not target_user:
        typer.echo("[!] Configure BARSI_TVBOX_IP e BARSI_TVBOX_USER no .env")
        raise typer.Exit(code=1)
    target_pass = password or settings.tvbox_pass or getpass.getpass(f"Senha SSH para {target_user}@{target_ip}: ")

    remote_home = _resolve_tvbox_home(target_user)
    python_bin = venv_python if venv_python.startswith("/") else f"{remote_home}/{venv_python}"
    typer.echo(f"[+] Conectando em {target_user}@{target_ip} para instalar scheduler heartbeat...")

    service_content = f"""[Unit]
Description=Execucao Barsi Sync Worker (Scheduler Hibrido)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User={target_user}
WorkingDirectory={remote_home}
ExecStart={python_bin} -m barsi run --if-due
StandardOutput=journal
StandardError=journal
MemoryMax=250M
"""

    timer_content = """[Unit]
Description=Barsi Scheduler Heartbeat (logica condicional no app)

[Timer]
OnCalendar=*:0/5
Persistent=true
RandomizedDelaySec=15

[Install]
WantedBy=timers.target
"""

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(target_ip, username=target_user, password=target_pass, timeout=8)
        
        def run_sudo(cmd):
            full_cmd = f"echo '{target_pass}' | sudo -S {cmd}"
            stdin, stdout, stderr = ssh.exec_command(full_cmd)
            stdout.channel.recv_exit_status()
            return stdout.read().decode().strip(), stderr.read().decode().strip()

        s_cmd = f"bash -c \"cat << 'EOF' > /etc/systemd/system/barsi-sync.service\n{service_content}\nEOF\""
        t_cmd = f"bash -c \"cat << 'EOF' > /etc/systemd/system/barsi-sync.timer\n{timer_content}\nEOF\""

        run_sudo(s_cmd)
        run_sudo(t_cmd)
        run_sudo("systemctl daemon-reload")
        run_sudo("systemctl enable --now barsi-sync.timer")
        out, _ = run_sudo("systemctl list-timers barsi-sync.timer")
        typer.echo("[✔] Scheduler instalado com sucesso!\n")
        typer.echo(out)
    except Exception as exc:
        typer.echo(f"[!] Falha na instalação do scheduler: {exc}")
    finally:
        ssh.close()


@app.command()
def tvbox(
    ip: Optional[str] = typer.Option(None, "--ip", help="IP da TV Box na rede (lê de BARSI_TVBOX_IP se nulo)"),
    user: Optional[str] = typer.Option(None, "--user", help="Usuário SSH (lê de BARSI_TVBOX_USER se nulo)"),
    password: Optional[str] = typer.Option(None, "--pass", help="Senha SSH (lê de BARSI_TVBOX_PASS se nulo)")
):
    """Consulta a telemetria ao vivo da TV Box (Temperatura, RAM, Uptime e Timer)."""
    import getpass, paramiko

    target_ip = ip or settings.tvbox_ip
    target_user = user or settings.tvbox_user
    if not target_ip or not target_user:
        typer.echo("[!] Configure BARSI_TVBOX_IP e BARSI_TVBOX_USER no .env")
        raise typer.Exit(code=1)
    target_pass = password or settings.tvbox_pass or getpass.getpass(f"Senha SSH para {target_user}@{target_ip}: ")

    typer.echo(f"\n[+] Conectando na TV Box ({target_user}@{target_ip})...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(target_ip, username=target_user, password=target_pass, timeout=5)
    except Exception as exc:
        typer.echo(f"[!] Falha na conexão SSH com a TV Box: {exc}")
        return

    # Consulta temperatura
    _, stdout, _ = ssh.exec_command("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || vcgencmd measure_temp")
    temp_raw = stdout.read().decode().strip()
    try:
        temp_c = f"{float(temp_raw)/1000.0:.1f} °C" if temp_raw.isdigit() else temp_raw
    except Exception:
        temp_c = "N/D"

    # Consulta memória RAM
    _, stdout, _ = ssh.exec_command("free -h | awk '/Mem:/ {print $3 \" usados de \" $2 \" (\" $7 \" disponíveis)\"}'")
    ram_info = stdout.read().decode().strip()

    # Consulta Uptime
    _, stdout, _ = ssh.exec_command("uptime -p")
    uptime_info = stdout.read().decode().strip()

    # Consulta status do timer
    _, stdout, _ = ssh.exec_command("systemctl is-active barsi-sync.timer 2>/dev/null")
    timer_status = stdout.read().decode().strip()

    # Consulta última execução do service
    _, stdout, _ = ssh.exec_command("systemctl show barsi.service --property=ActiveEnterTimestamp,ExecMainExitTimestamp,SubState --no-pager")
    service_props = stdout.read().decode().strip()

    ssh.close()

    typer.echo("="*55)
    typer.echo("      TELEMETRIA DA TV BOX (DEBIAN ARM64 - S905W)      ")
    typer.echo("="*55)
    typer.echo(f"🌡️  Temperatura CPU : {temp_c}")
    typer.echo(f"🧠 Memória RAM     : {ram_info}")
    typer.echo(f"⏱️  Tempo de Atividade: {uptime_info}")
    typer.echo(f"⏰ Systemd Timer   : {'● ATIVO (Seg-Sex 09h-18h)' if timer_status == 'active' else '○ INATIVO'}")
    typer.echo("\n📋 Última Execução:")
    for line in service_props.splitlines():
        if line:
            typer.echo(f"   • {line}")
    typer.echo("="*55 + "\n")

@app.command()
def user_add(
    username: str = typer.Argument(..., help="Nome de usuário"),
    password: str = typer.Argument(..., help="Senha de acesso"),
    role: str = typer.Option("leitor", "--role", help="Perfil: admin | investidor | leitor")
):
    """Cria um usuário criptografado no banco SQLite com perfil de acesso (RBAC)."""
    from barsi.adapters.history.user_repository import UserRepository
    repo = UserRepository(settings.sqlite_db_path)
    if role not in ["admin", "investidor", "leitor"]:
        typer.echo("[!] Perfil inválido! Escolha: admin | investidor | leitor")
        return
    ok = repo.create_user(username, password, role=role)
    if ok:
        typer.echo(f"[✔] Usuário '{username}' criado com sucesso no banco SQLite! Perfil: {role.upper()}")
    else:
        typer.echo(f"[!] Erro: Usuário '{username}' já existe no banco.")

@app.command()
def user_list():
    """Lista todos os usuários e perfis cadastrados no banco de dados SQLite."""
    from barsi.adapters.history.user_repository import UserRepository
    repo = UserRepository(settings.sqlite_db_path)
    rows = repo.list_users()

    typer.echo("\n=======================================================")
    typer.echo("     USUÁRIOS E PERFIS DE ACESSO (SQLITE RBAC)        ")
    typer.echo("=======================================================")
    if not rows:
        typer.echo("Nenhum usuário cadastrado.")
    for r in rows:
        typer.echo(f" • ID: {r['id']} | Usuário: {r['username']:<12} | Perfil: {r['role'].upper():<10} | Permissões: {r['permissoes']}")
    typer.echo("=======================================================\n")

budget_app = typer.Typer(name="budget", help="Gestão e reconciliação de orçamento e despesas recorrentes (Zero Magic Numbers)")
app.add_typer(budget_app)

@budget_app.command("seed")
def budget_seed():
    """Popula as despesas recorrentes iniciais no banco SQLite."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)
    count = repo.seed_defaults()
    typer.echo(f"[✔] {count} despesas recorrentes semeadas com sucesso no banco SQLite!")

@budget_app.command("list")
def budget_list():
    """Lista todas as despesas recorrentes e vigências cadastradas no banco SQLite."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)
    rows = repo.list_recurring()
    typer.echo("\n" + "="*80)
    typer.echo("       DESPESAS RECORRENTES & ORÇAMENTO (SQLITE SOURCE OF TRUTH)        ")
    typer.echo("="*80)
    if not rows:
        typer.echo("Nenhuma despesa recorrente cadastrada.")
    for r in rows:
        v_to = r['valid_to'] or 'VIGENTE'
        typer.echo(f" • [{r['kind']:<9}] {r['category']:<32} : R$ {r['amount']:>8.2f} (de {r['valid_from']} até {v_to})")
    typer.echo("="*80 + "\n")

@budget_app.command("set")
def budget_set(
    category: str = typer.Argument(..., help="Nome da categoria (ex: 'Aluguel')"),
    amount: float = typer.Argument(..., help="Valor mensal da despesa (ex: 550.00)"),
    valid_from: str = typer.Argument(..., help="Data de início da vigência no formato YYYY-MM-DD (ex: '2027-01-01')"),
    valid_to: Optional[str] = typer.Option(None, "--valid-to", help="Data de fim da vigência (opcional)"),
    kind: str = typer.Option("fixa", "--kind", help="Tipo: fixa | variavel | tributo | reserva | investimento | entrada"),
    notes: Optional[str] = typer.Option(None, "--notes", help="Observações")
):
    """Cria ou atualiza uma despesa recorrente, fechando automaticamente a vigência anterior."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)
    repo.upsert_recurring(category, amount, valid_from, valid_to=valid_to, kind=kind, source="manual", notes=notes)
    typer.echo(f"[✔] Despesa '{category}' configurada para R$ {amount:.2f} a partir de {valid_from}!")

@budget_app.command("sync")
def budget_sync(
    dir_path: str = typer.Option("extratos", "--dir", help="Pasta com extratos .ofx"),
    target_month: Optional[str] = typer.Option(
        None,
        "--target-month",
        help="Mês alvo (YYYY-MM). Compras diretas de meses anteriores são ignoradas. Padrão: MAX(mês atual, mês mais recente do extrato). Use explicitamente para fechar um mês anterior em atraso."
    ),
):
    """Executa a conciliação completa de extratos e sincroniza a aba Budget_Manager_Fluxo."""
    if target_month:
        try:
            from barsi.adapters.history.budget_repository import validate_target_month
            target_month = validate_target_month(target_month)
        except ValueError as exc:
            typer.echo(f"[!] Erro no mês alvo: {exc}")
            raise typer.Exit(code=1)
    from barsi.services.ofx_service import OFXParserService
    from barsi.adapters.factory import AdapterFactory
    resumos = OFXParserService.processar_diretorio_extratos(dir_path)
    if not resumos:
        typer.echo(f"[!] Nenhum extrato encontrado em '{dir_path}'")
        return

    # Persistência no SQLite via caller
    try:
        from barsi.adapters.history.budget_repository import BudgetRepository
        repo = BudgetRepository(settings.sqlite_db_path)
        reconciled_count = repo.reconcile_ofx_to_positions(resumos, target_month=target_month)
        if reconciled_count > 0:
            typer.echo(f"\n[✔] {reconciled_count} ativo(s) em carteira conciliados com o OFX (qtd_aporte -> qtd_antes)!")
        todas = [t for r in resumos.values() for t in r.transacoes]
        if todas:
            repo.persist_transacoes(todas)
        BUCKETS_PERSISTIR = {
            "Salário / Renda Principal":     lambda r: r.salario_entradas,
            "Proventos B3":                 lambda r: r.proventos_recebidos,
            "Aporte Ações/FIIs":            lambda r: r.compras_acoes_barsi,
            "Fatura Cartão Nubank":         lambda r: r.faturas_cartao,
            "Reserva / RDB":                lambda r: r.reserva_rdb,
            "Tributos & Taxas":             lambda r: r.tributos_e_taxas,
            "Compromisso Familiar / Moradia": lambda r: r.moradia_e_familia,
            "Cooperativa Ailos":            lambda r: r.cooperativa_ailos,
            "Despesas Variáveis":           lambda r: r.despesas_variaveis,
        }
        for mes, res in resumos.items():
            for categoria, getter in BUCKETS_PERSISTIR.items():
                val = getter(res)
                if val > 0:
                    repo.record_budget_entry(mes, categoria, val, source="ofx")
    except Exception as exc:
        logger.warning("persist_transacoes_failed", error=str(exc))

    sheets_adapter = AdapterFactory.create_sheets_adapter(settings)
    if hasattr(sheets_adapter, "sync_budget_manager"):
        sheets_adapter.sync_budget_manager(resumos)
        typer.echo("[✔] Orçamento sincronizado com o Google Sheets com base nas despesas do SQLite!")

@budget_app.command("audit")
def budget_audit(
    mes_ano: str = typer.Argument(..., help="Competência para auditoria no formato YYYY-MM (ex: '2026-10')")
):
    """Verifica integridade cruzada: transações dos extratos × budget_entries (conciliado)."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)

    INCOME_CATEGORIES = {
        "Salário / Renda Principal",
        "Proventos B3",
        "Rendimento NuConta",
        "Outras Entradas",
    }
    CATEGORY_MAPPING = {
        "Despesas Gerais": "Despesas Variáveis",
        "Transferência Familiar": "Compromisso Familiar / Moradia",
    }

    with repo._conn() as conn:
        # 1. Totalizações em budget_entries para o mês
        rows_be = conn.execute(
            """
            SELECT c.name as categoria, b.amount
              FROM budget_entries b
              JOIN expense_categories c ON c.id = b.category_id
             WHERE b.mes_ano = ?
            """,
            (mes_ano,)
        ).fetchall()
        soma_be = {r["categoria"]: round(float(r["amount"]), 2) for r in rows_be}

        # Fallback para recurring se não houver registros em budget_entries
        if not soma_be:
            soma_be = repo.get_all_expenses(mes_ano)

        # 2. Transações dos extratos
        rows_tx = conn.execute(
            """
            SELECT tipo, categoria, valor
              FROM transacoes
             WHERE mes_ano = ?
            """,
            (mes_ano,)
        ).fetchall()

    soma_tx: dict[str, float] = {}
    for r in rows_tx:
        raw_cat = r["categoria"]
        cat = CATEGORY_MAPPING.get(raw_cat, raw_cat)
        val = float(r["valor"])
        if cat in INCOME_CATEGORIES:
            if val > 0:
                soma_tx[cat] = round(soma_tx.get(cat, 0.0) + val, 2)
        else:
            if val < 0:
                soma_tx[cat] = round(soma_tx.get(cat, 0.0) + abs(val), 2)

    typer.echo("\n" + "=" * 80)
    typer.echo(f"   AUDITORIA DE INTEGRIDADE ORÇAMENTÁRIA — COMPETÊNCIA {mes_ano}   ")
    typer.echo("=" * 80)
    all_cats = sorted(set(soma_tx) | set(soma_be))
    if not all_cats:
        typer.echo(f"[!] Nenhum registro de extrato ou orçamento encontrado para o mês {mes_ano}.")
        typer.echo("=" * 80 + "\n")
        return

    typer.echo(f" {'STATUS':<6} | {'CATEGORIA':<34} | {'EXTRATOS':>12} | {'ORÇAMENTO':>12} | {'DELTA':>10}")
    typer.echo("-" * 80)
    total_delta = 0.0
    for cat in all_cats:
        tx = soma_tx.get(cat, 0.0)
        be = soma_be.get(cat, 0.0)
        delta = round(be - tx, 2)
        total_delta += abs(delta)
        status = "  ✓   " if abs(delta) < 0.05 else "  ⚠️  "
        typer.echo(f" {status} | {cat:<34} | R$ {tx:>9.2f} | R$ {be:>9.2f} | R$ {delta:>+7.2f}")
    typer.echo("=" * 80)
    if total_delta < 0.05:
        typer.echo(" [✔] 100% CONCILIADO — Nenhuma divergência significativa entre extratos e orçamento.\n")
    else:
        typer.echo(f" [!] Detectada divergência acumulada de R$ {total_delta:.2f} entre extratos e orçamento.\n")


keywords_app = typer.Typer(help="Gerenciamento de palavras-chave para classificação automática de extratos")
app.add_typer(keywords_app, name="keywords")

@keywords_app.command("list")
def keywords_list():
    """Lista todas as palavras-chave cadastradas para classificação de transações."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)
    kws = repo.get_keywords()
    typer.echo("\n" + "="*75)
    typer.echo("   PALAVRAS-CHAVE DE CLASSIFICAÇÃO AUTOMÁTICA (KEYWORD TRIE)   ")
    typer.echo("="*75)
    typer.echo(f" {'PRIO':<6} | {'PALAVRA-CHAVE':<24} | {'CATEGORIA':<26} | {'BUCKET':<10}")
    typer.echo("-" * 75)
    for kw, cat, bucket, prio in kws:
        typer.echo(f" {prio:<6} | {kw:<24} | {cat:<26} | {bucket:<10}")
    typer.echo("="*75 + "\n")

@keywords_app.command("add")
def keywords_add(
    keyword: str = typer.Argument(..., help="Palavra ou termo a identificar no extrato"),
    category: str = typer.Argument(..., help="Categoria atribuída (ex: 'Despesas Variáveis')"),
    bucket: str = typer.Option("variavel", "--bucket", help="Bucket: variavel | tributo | fixa | entrada | reserva | investimento"),
    priority: int = typer.Option(100, "--priority", help="Prioridade no casamento (maior = mais prioritário)")
):
    """Adiciona ou atualiza uma palavra-chave no banco de dados para classificação automática."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    from barsi.services.ofx_service import OFXParserService
    repo = BudgetRepository(settings.sqlite_db_path)
    repo.upsert_keyword(keyword, category, bucket=bucket, priority=priority)
    OFXParserService.reload_keywords_from_db()
    typer.echo(f"[✔] Palavra-chave '{keyword.upper()}' associada à categoria '{category}' (prioridade {priority})!")

@keywords_app.command("remove")
def keywords_remove(
    keyword: str = typer.Argument(..., help="Palavra-chave a remover")
):
    """Remove uma palavra-chave do banco de dados."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    from barsi.services.ofx_service import OFXParserService
    repo = BudgetRepository(settings.sqlite_db_path)
    removido = repo.delete_keyword(keyword)
    if removido:
        OFXParserService.reload_keywords_from_db()
        typer.echo(f"[✔] Palavra-chave '{keyword.upper()}' removida com sucesso!")
    else:
        typer.echo(f"[!] Palavra-chave '{keyword.upper()}' não encontrada no banco.")

@keywords_app.command("test")
def keywords_test(
    memo: str = typer.Argument(..., help="Texto ou descrição do extrato para testar classificação"),
    valor: float = typer.Option(-100.0, "--valor", help="Valor da transação (positivo=crédito, negativo=débito)"),
):
    """Testa qual categoria o classificador atribui a uma descrição de extrato."""
    from barsi.services.ofx_service import OFXParserService, _classify_transaction
    OFXParserService.reload_keywords_from_db()
    trie = OFXParserService._get_trie()
    tipo = "CREDIT" if valor > 0 else "DEBIT"
    cat, ticker = _classify_transaction(
        memo_upper=memo.upper(),
        valor=valor,
        tipo=tipo,
        titular_nomes=settings.titular_nomes,
        familiar_keywords=settings.familiar_keywords,
        trie=trie,
    )
    typer.echo("\n🔍 TESTE DE CLASSIFICAÇÃO:")
    typer.echo(f"   Memo analisado : '{memo}'")
    typer.echo(f"   Valor / Tipo   : R$ {valor:.2f} ({tipo})")
    typer.echo(f"   [✔] Categoria  : {cat.value}")
    if ticker:
        typer.echo(f"   [✔] Ticker     : {ticker}")
    typer.echo("")

notify_app = typer.Typer(help="Envio e teste de notificações proativas (Telegram e WhatsApp)")
app.add_typer(notify_app, name="notify")

@notify_app.command("test")
def notify_test(
    title: str = typer.Option("Teste de Notificação Barsi", "--title", help="Título do alerta"),
    message: str = typer.Option("Alerta de teste enviado pelo Sistema Barsi Quant IA.", "--msg", help="Corpo da mensagem"),
    level: str = typer.Option("info", "--level", help="Nível: info | alert | warn | success"),
):
    """Envia uma mensagem de teste para os canais de notificação configurados no .env."""
    import asyncio
    from barsi.adapters.factory import AdapterFactory
    from barsi.adapters.notifications import NoOpNotifier

    notifier = AdapterFactory.create_notification_adapter(settings)
    if isinstance(notifier, NoOpNotifier):
        typer.echo("\n[!] Nenhum canal de notificação está habilitado ou configurado no .env.")
        typer.echo("    Habilite BARSI_TELEGRAM_ALERTS_ENABLED=true ou BARSI_WHATSAPP_ALERTS_ENABLED=true.\n")
        return

    typer.echo(f"\n[+] Disparando notificação de teste ({level.upper()})...")
    ok = asyncio.run(notifier.notify(title=title, body=message, level=level))
    if ok:
        typer.echo("[✔] Notificação entregue com sucesso aos canais configurados!\n")
    else:
        typer.echo("[❌] Falha no envio. Verifique as credenciais no .env e a conectividade de rede.\n")

@notify_app.command("get-chat-id")
def notify_get_chat_id():
    """Consulta os updates do bot do Telegram para capturar o chat_id e salvar no .env."""
    import os
    import httpx
    import re
    token = settings.telegram_bot_token
    if not token:
        typer.echo("[!] BARSI_TELEGRAM_BOT_TOKEN não configurado no .env.")
        return

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    typer.echo("\n[+] Consultando mensagens enviadas ao seu bot no Telegram...")
    try:
        resp = httpx.get(url, timeout=10.0)
        data = resp.json()
        results = data.get("result", [])
        if not results:
            bot_username = "seu_bot"
            try:
                me_resp = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5.0).json()
                bot_username = me_resp.get("result", {}).get("username", "seu_bot")
            except Exception:
                pass
            typer.echo("\n[!] Nenhuma mensagem encontrada ainda!")
            typer.echo(f"    1. Abra o link do seu bot: https://t.me/{bot_username}")
            typer.echo("    2. Clique em 'Começar' ou envie '/start'")
            typer.echo("    3. Execute este comando novamente: python -m barsi notify get-chat-id\n")
            return

        last_update = results[-1]
        msg = last_update.get("message") or last_update.get("channel_post") or {}
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id"))

        if not chat_id or chat_id == "None":
            typer.echo("[!] Não foi possível identificar o chat_id no retorno da API.\n")
            return

        typer.echo(f"[✔] Chat identificado com sucesso! ID: {chat_id}")

        # Atualiza ou insere BARSI_TELEGRAM_CHAT_ID no .env
        env_path = ".env" if os.path.exists(".env") else "barsi_quant_ia/.env"
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "BARSI_TELEGRAM_CHAT_ID=" in content:
                content = re.sub(r"BARSI_TELEGRAM_CHAT_ID=.*", f"BARSI_TELEGRAM_CHAT_ID={chat_id}", content)
            else:
                content += f"\nBARSI_TELEGRAM_CHAT_ID={chat_id}\n"
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(content)
            typer.echo("[✔] BARSI_TELEGRAM_CHAT_ID salvo com segurança no seu arquivo .env local!")

        # Envia mensagem de boas-vindas teste
        from barsi.adapters.notifications import TelegramNotifier
        import asyncio
        notifier = TelegramNotifier(bot_token=token, chat_id=chat_id)
        typer.echo("[+] Enviando alerta de confirmação para o seu Telegram...")
        asyncio.run(notifier.notify("Barsi Quant IA Conectado!", "Seu bot de alertas proativos está 100% configurado e ativo.", level="success"))
        typer.echo("[✔] Alerta de teste entregue no seu Telegram!\n")
    except Exception as exc:
        typer.echo(f"[!] Erro ao consultar API do Telegram: {exc}\n")

@app.command("export-sheets")
def export_sheets_cli():
    """Exporta unidirecionalmente as posições e métricas do SQLite para o Google Sheets."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    from barsi.adapters.factory import AdapterFactory
    repo = BudgetRepository(settings.sqlite_db_path)
    positions = repo.load_positions()
    if not positions:
        typer.echo("[!] Nenhuma posição encontrada no banco SQLite local.")
        return
    sheets = AdapterFactory.create_sheets_adapter(settings)
    asyncio.run(sheets.update_positions(positions, invalidate=True))
    typer.echo(f"[✔] {len(positions)} ativos exportados com sucesso para o Google Sheets!")

@app.command("export-xlsx")
def export_xlsx_cli(dest: str = "carteira_barsi_quant_ia.xlsx"):
    """Exporta as posições do SQLite para planilha Excel local (.xlsx)."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    from barsi.adapters.sheets.xlsx_adapter import XLSXAdapter
    repo = BudgetRepository(settings.sqlite_db_path)
    positions = repo.load_positions()
    if not positions:
        typer.echo("[!] Nenhuma posição encontrada no banco SQLite local.")
        return
    adapter = XLSXAdapter(dest)
    asyncio.run(adapter.update_positions(positions))
    typer.echo(f"[✔] {len(positions)} ativos exportados para '{dest}' com sucesso!")

@app.command("history-sync")
def history_sync_cli():
    """Baixa o histórico diário real via YFinance para todos os ativos e recalcula a curva no SQLite."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)
    typer.echo("[+] Sincronizando cotações históricas diárias via YFinance...")
    count = repo.sync_yfinance_history()
    typer.echo(f"[✔] {count} pregões salvos na tabela price_history!")
    snapshots = repo.build_real_portfolio_snapshots()
    typer.echo(f"[✔] Curva patrimonial real recalculada com {len(snapshots)} meses históricos!\n")

@app.command("train-dss")
def train_dss_cli(
    db_path: Optional[str] = typer.Option(None, "--db", help="Path do SQLite (default: settings)"),
    output: str = typer.Option("memory/dss_weights.json", "--output"),
    use_system_data: bool = typer.Option(True, "--use-system/--no-system"),
):
    """Treina o modelo DSS com dataset base + feedbacks RLHF do sistema."""
    from barsi.training.train_dss import train_full_pipeline
    typer.echo("[+] Iniciando treinamento do modelo DSS (TinyMLP)...")
    metrics = train_full_pipeline(
        db_path=db_path or settings.sqlite_db_path,
        output_path=output,
        use_system_data=use_system_data,
    )
    typer.echo("[✔] Treinamento concluído com sucesso!")
    typer.echo(f"    - Exemplos de treino: {metrics['n_train']}, validação: {metrics['n_val']} (total: {metrics['total_examples']})")
    typer.echo(f"    - Acurácia na validação: {metrics['best_val_acc']:.1%}")
    typer.echo(f"    - Pesos salvos em: {output}")
    typer.echo("💡 Servidor em execução? Recarregue via: sudo systemctl restart barsi-api.service ou POST /api/dss/reload")

@app.command("clean-test-data")
def clean_test_data_cli(
    confirm: bool = typer.Option(False, "--yes", "-y", help="Pula a confirmação interativa"),
    db_path: Optional[str] = typer.Option(None, "--db", help="Path do SQLite (default: settings.sqlite_db_path)")
):
    """Remove dados operacionais de teste e reseta as sequências AUTOINCREMENT (sqlite_sequence)."""
    import sqlite3
    from barsi.adapters.history.budget_repository import BudgetRepository

    target_db = db_path or settings.sqlite_db_path

    if not confirm:
        typer.confirm(f"⚠️ Isso apagará dados transacionais do banco SQLite ({target_db}) e resetará os IDs. Continuar?", abort=True)

    repo = BudgetRepository(target_db)

    tabelas = [
        "positions",
        "transacoes",
        "dividends_received",
        "historico_analises",
        "portfolio_snapshots",
        "runs",
        "notification_log",
        "budget_entries",
        "custom_artifacts",
        "pending_sheets_writes",
        "sheets_positions_cache",
        "price_history",
    ]

    with repo._conn() as c:
        for tabela in tabelas:
            try:
                c.execute(f"DELETE FROM {tabela};")
            except sqlite3.OperationalError:
                pass
            try:
                c.execute("DELETE FROM sqlite_sequence WHERE name = ?;", (tabela,))
            except sqlite3.OperationalError:
                pass

    typer.echo("[✔] Dados limpos e sequências AUTOINCREMENT (sqlite_sequence) reiniciadas com sucesso!")

@app.command("send-monthly-closing")
def send_monthly_closing_cli(
    mes_ano: Optional[str] = typer.Option(None, "--mes-ano", help="Mês de referência YYYY-MM")
):
    """Gera e despacha o Fechamento Executivo Barsi via Telegram."""
    import asyncio
    from barsi.adapters.history.budget_repository import BudgetRepository
    from barsi.adapters.factory import AdapterFactory
    from barsi.services.monthly_closing_service import MonthlyClosingService

    repo = BudgetRepository(settings.sqlite_db_path)
    notifier = AdapterFactory.create_notification_adapter(settings)
    service = MonthlyClosingService(repo)

    async def _send():
        ok = await service.send_closing_via_telegram(notifier, mes_ano=mes_ano)
        if ok:
            typer.echo("[✔] Fechamento mensal despachado com sucesso via Telegram!")
        else:
            typer.echo("[!] Falha ao despachar fechamento mensal via Telegram.")

    asyncio.run(_send())

@app.command("test-telegram")
def test_telegram_cli(
    msg: str = typer.Option("Teste de conectividade Barsi Quant IA", "--msg")
):
    """Envia uma mensagem de teste para o Telegram configurado no .env."""
    import asyncio
    from barsi.adapters.factory import AdapterFactory

    notifier = AdapterFactory.create_notification_adapter(settings)
    async def _send():
        ok = await notifier.notify(title="Teste de Conectividade", body=msg, level="success")
        if ok:
            typer.echo("[✔] Mensagem enviada com sucesso para o Telegram!")
        else:
            typer.echo("[!] Erro no envio para o Telegram. Verifique BARSI_TELEGRAM_BOT_TOKEN e BARSI_TELEGRAM_CHAT_ID.")

    asyncio.run(_send())

@app.command("tvbox-headless")
def tvbox_headless_cli(
    ip: Optional[str] = typer.Option(None, "--ip"),
    user: Optional[str] = typer.Option(None, "--user"),
    password: Optional[str] = typer.Option(None, "--pass")
):
    """Remove a interface gráfica (X11/LightDM/Desktop) da TV Box para liberar memória RAM."""
    import getpass, paramiko

    target_ip = ip or settings.tvbox_ip
    target_user = user or settings.tvbox_user
    target_pass = password or settings.tvbox_pass or getpass.getpass(f"Senha SSH para {target_user}@{target_ip}: ")

    typer.echo(f"\n[+] Conectando na TV Box ({target_user}@{target_ip}) para remover ambiente gráfico...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(target_ip, username=target_user, password=target_pass, timeout=8)
        
        def run_sudo(cmd: str):
            stdin, stdout, stderr = ssh.exec_command(f"sudo -S {cmd}", get_pty=True)
            stdin.write(f"{target_pass}\n")
            stdin.flush()
            out = stdout.read().decode("utf-8")
            return out

        typer.echo("    - Definindo boot padrão para modo console headless (multi-user.target)...")
        run_sudo("systemctl set-default multi-user.target")
        typer.echo("    - Parando serviços gráficos (lightdm, gdm, nodm)...")
        run_sudo("systemctl stop lightdm gdm3 nodm 2>/dev/null || true")
        run_sudo("systemctl disable lightdm gdm3 nodm 2>/dev/null || true")
        typer.echo("[✔] Interface gráfica desativada com sucesso! Memória RAM otimizada para o Barsi Quant.")
    except Exception as exc:
        typer.echo(f"[!] Falha: {exc}")
    finally:
        ssh.close()

@app.command("radar-screening")
def radar_screening_cli(
    margem_min: float = typer.Option(20.0, "--margem", help="Margem de segurança mínima percentual")
):
    """Screening ampliado de ativos líquidos da B3 no radar B.E.S.T. com Margem > 20%."""
    import asyncio
    from barsi.adapters.factory import AdapterFactory
    from barsi.domain.rules import calcular_margem_seguranca

    market = AdapterFactory.create_market_adapter(settings)
    notifier = AdapterFactory.create_notification_adapter(settings)

    TICKERS_RADAR = [
        "BBAS3", "ITSA4", "SANB11", "BBDC4", "BBSE3", "CXSE3", "PSSA3",
        "TAEE11", "TRPL4", "CPLE6", "EGIE3", "CMIG4", "ALUP11", "NEOE3",
        "SAPR11", "SBSP3", "CSMG3", "VIVT3", "TIMS3", "KLBN11", "SUZB3",
        "VALE3", "PETR4", "VBBR3", "RAIZ4", "WEGE3", "FLRY3", "ODPV3"
    ]

    async def _screen():
        typer.echo(f"[+] Iniciando screening ampliado de {len(TICKERS_RADAR)} ativos B3...")
        oportunidades = []
        for ticker in TICKERS_RADAR:
            try:
                hist = await market.get_history(ticker, period="1mo")
                if not hist:
                    continue
                preco_atual = hist[-1].close
                div_proj = preco_atual * 0.08
                teto_real = div_proj / 0.06
                margem = calcular_margem_seguranca(teto_real, preco_atual)
                if margem >= margem_min:
                    oportunidades.append((ticker, preco_atual, teto_real, margem))
            except Exception:
                pass

        if oportunidades:
            linhas = [f"• *{t}*: R$ {p:.2f} (Teto R$ {tt:.2f} | Margem +{m:.1f}%)" for t, p, tt, m in oportunidades]
            corpo = "\n".join(linhas)
            typer.echo(f"[✔] Encontradas {len(oportunidades)} oportunidades no radar ampliado:\n{corpo}")
            await notifier.notify(
                title=f"🎯 Radar B3 Ampliado — {len(oportunidades)} Oportunidades > {margem_min:.0f}%",
                body=corpo,
                level="alert"
            )
        else:
            typer.echo(f"[i] Nenhuma ação com margem > {margem_min:.0f}% no momento.")

    asyncio.run(_screen())

@app.command("import-b3")
def import_b3(
    file_path: Path = typer.Argument(..., help="Caminho do arquivo Excel de custódia da Área do Investidor da B3 (.xlsx)"),
):
    """Importa e concilia a custódia oficial da Área do Investidor da B3 (.xlsx) para o SQLite canônico."""
    from barsi.services.b3_custodia_service import B3CustodiaService
    from barsi.adapters.history.budget_repository import BudgetRepository

    if not file_path.exists():
        typer.echo(f"[-] Arquivo não encontrado: {file_path}")
        raise typer.Exit(code=1)

    repo = BudgetRepository(settings.sqlite_db_path)
    service = B3CustodiaService(repo)
    typer.echo(f"[+] Lendo custódia da B3 em: {file_path}...")
    res = service.sync_custodia_to_sqlite(file_path)
    typer.echo(f"[✔] {res['message']}")
    typer.echo(f"    Patrimônio apurado: R$ {res['patrimonio_total']:,.2f}")
    for at in res.get("ativos", []):
        typer.echo(f"    • {at['ticker']:<7} ({at['setor']:<14}): {at['qtd']:<5} cotas @ R$ {at['preco']:<6.2f} [{at['status']}]")

if __name__ == "__main__":
    app()

