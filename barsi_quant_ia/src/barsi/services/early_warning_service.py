"""Serviço de Varredura e Notificação de Alertas de Quebra de Tese (Early Warning).

Varre automaticamente as notícias recentes dos ativos da carteira no Google News RSS,
aplica o classificador NLP léxico-neural de risco e despacha alertas prioritários
para Telegram e WhatsApp.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.early_warning import EarlyWarningDetector, ThesisAlertResult
from barsi.ports.news import NewsPort
from barsi.ports.notifications import NotificationsPort
from barsi.infra.logging import logger


class EarlyWarningService:
    """Orquestrador de monitoramento de risco e alerta vermelho de tese."""

    def __init__(
        self,
        budget_repo: BudgetRepository,
        news_adapter: Optional[NewsPort] = None,
        notification_adapter: Optional[Any] = None
    ):
        self.repo = budget_repo
        self.news_port = news_adapter
        self.notifier = notification_adapter
        self.detector = EarlyWarningDetector()

    async def scan_portfolio(self) -> List[Dict[str, Any]]:
        """Varre todos os ativos da carteira buscando riscos de quebra de tese."""
        positions = self.repo.load_positions()
        if not positions:
            return []

        active_alerts: List[Dict[str, Any]] = []

        for p in positions:
            ticker = p.ticker.upper()
            try:
                news_summary = await self.news_port.get_latest_news(ticker)
                text = news_summary.headlines if news_summary else ""
                alert = self.detector.analyze_news(ticker, text)

                if alert:
                    # Verifica se já existe um alerta ativo para este ticker e tipo de risco
                    existing = self.repo.list_thesis_alerts(ticker=ticker, active_only=True)
                    ja_registrado = any(e["risk_type"] == alert.risk_type for e in existing)

                    alert_id = None
                    if not ja_registrado:
                        alert_id = self.repo.record_thesis_alert(
                            ticker=alert.ticker,
                            risk_type=alert.risk_type,
                            severity=alert.severity,
                            headline=alert.headline,
                            snippet=alert.snippet,
                            source=news_summary.source if news_summary else "google_rss"
                        )

                        # Envio proativo para Telegram / WhatsApp
                        if self.notifier:
                            msg = (
                                f"🚨 *ALERTA VERMELHO DE TESE BARSI*\n\n"
                                f"📌 *Ativo:* {alert.ticker} ({p.empresa})\n"
                                f"⚠️ *Risco:* {alert.headline}\n"
                                f"🔍 *Trecho da Notícia:* \"{alert.snippet}\"\n"
                                f"💡 *Recomendação:* {alert.recommendation}\n"
                            )
                            try:
                                if hasattr(self.notifier, "notify"):
                                    await self.notifier.notify(
                                        title=f"🚨 ALERTA BARSI: {alert.ticker}",
                                        body=f"{alert.headline}\n{alert.snippet}",
                                        level="error"
                                    )
                                elif hasattr(self.notifier, "send_message"):
                                    await self.notifier.send_message(msg)
                                logger.info("early_warning_notified", ticker=ticker, risk_type=alert.risk_type)
                            except Exception as notif_err:
                                logger.warning("early_warning_notif_failed", ticker=ticker, error=str(notif_err))

                    active_alerts.append({
                        "id": alert_id or (existing[0]["id"] if existing else None),
                        "ticker": alert.ticker,
                        "empresa": p.empresa,
                        "risk_type": alert.risk_type,
                        "severity": alert.severity,
                        "headline": alert.headline,
                        "snippet": alert.snippet,
                        "recommendation": alert.recommendation,
                        "is_new": not ja_registrado
                    })

            except Exception as e:
                logger.warning("early_warning_scan_item_failed", ticker=ticker, error=str(e))

        return active_alerts

    def list_alerts(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Lista os alertas históricos ou ativos registrados no SQLite."""
        return self.repo.list_thesis_alerts(active_only=active_only)

    def dismiss_alert(self, alert_id: int) -> bool:
        """Marca o alerta como arquivado/reconhecido."""
        return self.repo.dismiss_thesis_alert(alert_id)
