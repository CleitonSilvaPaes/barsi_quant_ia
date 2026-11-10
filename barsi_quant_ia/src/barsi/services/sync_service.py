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
            else:
                pos = atualizar_posicao_quant(pos, preco_novo=pos.preco_atual)

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
        target_ticker: Optional[str] = None
    ) -> RunSummary:
        start_time = time.time()
        run_id = str(uuid.uuid4())
        errors = []

        logger.info("run_started", run_id=run_id, dry_run=dry_run, target=target_ticker)

        # 1. Carrega posições
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

        # 3. Atualiza na planilha (se não for dry-run)
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

        acumular_cnt = sum(1 for p in valid_positions if getattr(p.status_barsi, "value", str(p.status_barsi)) == "ACUMULAR")
        aguardar_cnt = len(valid_positions) - acumular_cnt
        actions_map = {"ACUMULAR": acumular_cnt, "AGUARDAR": aguardar_cnt}

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
                from barsi.adapters.history.budget_repository import BudgetRepository
                repo = BudgetRepository()
                today_str = datetime.now().strftime("%Y-%m-%d")
                for p in valid_positions:
                    if p.preco_atual > 0:
                        repo.save_price_history(p.ticker, today_str, p.preco_atual)
            except Exception as e:
                logger.warn("save_price_history_run_failed", error=str(e))

        logger.info(
            "run_completed",
            run_id=run_id,
            tickers=summary.tickers_analyzed,
            duration_ms=duration_ms,
            rss_mb=rss_mb,
            errors_count=len(errors)
        )
        return summary

