"""Serviço de Sincronização e Orquestração do Pipeline Barsi."""
import asyncio
import time
import uuid
from datetime import datetime
import psutil
from typing import List, Optional
from barsi.ports.market import MarketDataPort
from barsi.ports.news import NewsPort
from barsi.ports.ai import AIPort
from barsi.ports.sheets import SheetsPort
from barsi.ports.history import HistoryPort
from barsi.ports.notifications import NotificationsPort
from barsi.domain.models import Position, RunSummary, AcaoBarsi
from barsi.domain.rules import atualizar_posicao_quant
from barsi.services.insight_service import InsightService
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.infra.logging import logger
from barsi.infra.events import event_bus

class SyncService:
    def __init__(
        self,
        market: MarketDataPort,
        news: NewsPort,
        ai: AIPort,
        sheets: SheetsPort,
        history: HistoryPort,
        notifier: Optional[NotificationsPort] = None,
        budget_repo: Optional[BudgetRepository] = None,
        max_concurrency: int = 3
    ):
        self.market = market
        self.news = news
        self.sheets = sheets
        self.history = history
        self.notifier = notifier
        self.budget_repo = budget_repo
        self.insight_service = InsightService(ai, history)
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def _process_single_position(
        self,
        pos: Position,
        run_id: str,
        dry_run: bool
    ) -> Position:
        async with self.semaphore:
            ticker = pos.ticker
            # 1. Cotação
            quote = await self.market.get_quote(ticker)
            if quote and quote.price > 0:
                pos = atualizar_posicao_quant(pos, preco_novo=quote.price)
                event_bus.publish("PriceUpdated", {"ticker": ticker, "price": quote.price})

            # 2. Notícias
            news_summary = await self.news.get_latest_news(ticker)

            # 3. Insight IA / Regras
            insight_res = await self.insight_service.generate_position_insight(pos, news_summary.headlines)
            pos.insight_atual = insight_res.insight
            event_bus.publish("InsightGenerated", {"ticker": ticker, "insight": insight_res.insight})

            # Notificação proativa (Telegram/WhatsApp) com deduplicação diária (máx 1 alerta/ticker/dia)
            if self.notifier and not dry_run and pos.margem_seguranca >= 20.0 and pos.status_barsi == AcaoBarsi.ACUMULAR:
                should_alert = True
                if self.budget_repo:
                    should_alert = self.budget_repo.should_notify(ticker, "margem_20")
                elif hasattr(self.history, "db_path") and self.history.db_path:
                    repo = BudgetRepository(db_path=self.history.db_path)
                    should_alert = repo.should_notify(ticker, "margem_20")

                if should_alert:
                    msg_body = (
                        f"📊 *Cotação Atual:* R$ {pos.preco_atual:.2f} | *Teto:* R$ {pos.preco_teto:.2f}\n"
                        f"🛡️ *Margem de Segurança:* {pos.margem_seguranca:.1f}%\n"
                        f"💰 *DY Estimado:* {pos.dy:.1f}%\n"
                        f"💡 *Insight:* {insight_res.insight}"
                    )
                    await self.notifier.notify(
                        title=f"{ticker} em Zona de Aporte (Margem {pos.margem_seguranca:.1f}%)",
                        body=msg_body,
                        level="alert"
                    )

            # 4. Grava no Histórico SQLite/R2
            if not dry_run:
                await self.history.save_analysis(
                    run_id=run_id,
                    posicao=pos,
                    insight=insight_res,
                    noticias=news_summary.headlines
                )

            return pos

    async def execute_run(
        self,
        dry_run: bool = False,
        target_ticker: Optional[str] = None,
        notify_radar: bool = False
    ) -> RunSummary:
        start_time = time.time()
        run_id = str(uuid.uuid4())
        errors = []

        logger.info("run_started", run_id=run_id, dry_run=dry_run, target=target_ticker)

        # 1. Carrega posições (Path B: SQLite é fonte primária se budget_repo injetado)
        repo = self.budget_repo
        if repo:
            all_positions = repo.load_positions()
            if not all_positions:
                try:
                    all_positions = await self.sheets.load_positions()
                    if all_positions:
                        repo.save_positions(all_positions)
                except Exception as exc:
                    logger.warning("sheets_load_fallback_failed", error=str(exc))
        else:
            all_positions = await self.sheets.load_positions()

        if not all_positions:
            logger.warn("no_positions_found")
            return RunSummary(run_id=run_id, errors=["Nenhuma posição encontrada"])

        if target_ticker:
            target_clean = target_ticker.strip().upper()
            positions_to_process = [p for p in all_positions if p.ticker == target_clean]
        else:
            positions_to_process = all_positions

        # 2. Processa em paralelo respeitando o Semaphore
        tasks = [
            self._process_single_position(p, run_id, dry_run)
            for p in positions_to_process
        ]
        processed_positions = await asyncio.gather(*tasks, return_exceptions=True)

        valid_positions: List[Position] = []
        for res in processed_positions:
            if isinstance(res, Exception):
                errors.append(str(res))
                logger.error("position_processing_error", error=str(res))
            else:
                valid_positions.append(res)

        # 3. Salva no SQLite PRIMEIRO (Fonte Canônica da Verdade)
        if not dry_run and valid_positions and repo:
            try:
                repo.save_positions(valid_positions)
            except Exception as exc:
                errors.append(f"SQLite save error: {str(exc)}")
                logger.error("sqlite_save_failed", error=str(exc))

        # Atualiza na planilha (se não for dry-run, best-effort)
        if not dry_run and valid_positions:
            try:
                await self.sheets.update_positions(valid_positions)
            except Exception as exc:
                errors.append(f"Sheets update error: {str(exc)}")
                logger.error("sheets_update_failed", error=str(exc))

        # 4. Métricas de Hardware (psutil) e Resumo
        duration_ms = int((time.time() - start_time) * 1000)
        mem_info = psutil.Process().memory_info()
        rss_mb = round(mem_info.rss / (1024 * 1024), 2)

        actions_map = {
            "ACUMULAR": sum(1 for p in valid_positions if getattr(p.status_barsi, "value", str(p.status_barsi)) == "ACUMULAR"),
            "AGUARDAR": sum(1 for p in valid_positions if getattr(p.status_barsi, "value", str(p.status_barsi)) == "AGUARDAR"),
            "MANTER": sum(1 for p in valid_positions if getattr(p.status_barsi, "value", str(p.status_barsi)) == "MANTER"),
        }

        summary = RunSummary(
            run_id=run_id,
            tickers_analyzed=len(valid_positions),
            positions_updated=len(valid_positions) if not dry_run else 0,
            actions_recommended=actions_map,
            duration_ms=duration_ms,
            memory_peak_mb=rss_mb,
            errors=errors
        )

        if not dry_run:
            await self.history.record_run(summary)
            try:
                today_str = datetime.now().strftime("%Y-%m-%d")
                if repo:
                    for p in valid_positions:
                        if p.preco_atual > 0:
                            repo.save_price_history(p.ticker, today_str, p.preco_atual)
            except Exception as e:
                logger.warn("save_price_history_run_failed", error=str(e))

            if notify_radar and valid_positions:
                try:
                    await self.send_hourly_radar_summary(valid_positions)
                except Exception as exc:
                    logger.warning("hourly_radar_failed", error=str(exc))

        logger.info(
            "run_completed",
            run_id=run_id,
            tickers=summary.tickers_analyzed,
            duration_ms=duration_ms,
            rss_mb=rss_mb,
            errors_count=len(errors)
        )
        return summary

    async def send_hourly_radar_summary(self, positions: Optional[List[Position]] = None) -> bool:
        """Envia resumo consolidado com o top 5 de oportunidades para canais de notificação."""
        if not self.notifier:
            return False
        if positions is None:
            positions = self.budget_repo.load_positions() if self.budget_repo else []
        acumular = [p for p in positions if getattr(p.status_barsi, "value", str(p.status_barsi)) == "ACUMULAR"]
        if not acumular:
            return False
        top = sorted(acumular, key=lambda x: getattr(x, "margem_seguranca", 0.0), reverse=True)[:5]
        linhas = "\n".join(
            f"• {p.ticker}: R$ {p.preco_atual:.2f} (margem {getattr(p, 'margem_seguranca', 0.0):.1f}%)"
            for p in top
        )
        try:
            await self.notifier.notify(
                title=f"📊 Radar {datetime.now().strftime('%H:%M')} — {len(acumular)} ativos no teto",
                body=linhas,
                level="info"
            )
            return True
        except Exception as exc:
            logger.warning("hourly_radar_notify_failed", error=str(exc))
            return False

