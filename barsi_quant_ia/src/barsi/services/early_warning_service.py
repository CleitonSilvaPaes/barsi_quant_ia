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

    async def scan_ofx_anomalies(
        self,
        transacoes: Optional[List[Dict[str, Any]]] = None,
        limit: int = 50,
        model_path: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Avalia transações bancárias/cartão recentes contra o modelo Isolation Forest.
        
        Gera alertas quando transações forem anômalas e não marcadas com feedback 'OK_ANOMALIA'.
        """
        from barsi.domain.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector.get_instance(model_path=model_path)

        txs_to_check = transacoes
        if txs_to_check is None:
            txs_to_check = self.repo.list_transacoes(limit=limit)

        anomalies: List[Dict[str, Any]] = []
        for t in txs_to_check:
            # Pula transações já validadas pelo usuário
            if t.get("feedback_usuario") == "OK_ANOMALIA":
                continue

            detection = detector.detect(t)
            if detection["is_anomaly"]:
                tx_id = t.get("id")
                if tx_id:
                    self.repo.update_transacao_anomaly_score(tx_id, detection["score"])

                ticker = (t.get("ticker") or t.get("ticker_relacionado") or "EXTRATO").strip().upper()
                fitid = t.get("fitid") or f"id_{tx_id}"
                risk_type = "ANOMALIA_EXTRATO"
                headline = f"Transação Incomum: {str(t.get('descricao', ''))[:35]}"
                snippet = f"Valor R$ {abs(float(t.get('valor', 0.0))):,.2f} ({t.get('origem', 'CONTA')}) | Score: {detection['score']:.2f} | {detection['reason']}"

                # Deduplicação: verifica se já foi registrado alerta ativo para este fitid/id
                existing = self.repo.list_thesis_alerts(ticker=ticker, active_only=True)
                ja_registrado = any(
                    e["risk_type"] == risk_type and fitid in e.get("snippet", "")
                    for e in existing
                )

                alert_id = None
                if not ja_registrado:
                    alert_id = self.repo.record_thesis_alert(
                        ticker=ticker,
                        risk_type=risk_type,
                        severity=detection["severity"],
                        headline=headline,
                        snippet=f"[{fitid}] {snippet}",
                        source="isolation_forest"
                    )

                    if self.notifier:
                        msg = (
                            f"🚨 *ALERTA DE ANOMALIA EM EXTRATO*\n\n"
                            f"💳 *Transação:* {t.get('descricao')}\n"
                            f"💵 *Valor:* R$ {abs(float(t.get('valor', 0.0))):,.2f} ({t.get('tipo')})\n"
                            f"📅 *Data:* {t.get('data')} | *Origem:* {t.get('origem', 'CONTA')}\n"
                            f"⚠️ *Severidade:* {detection['severity']} (Score: {detection['score']:.2f})\n"
                            f"🔍 *Motivo:* {detection['reason']}\n\n"
                            f"_Confirme no painel caso a transação seja legítima._"
                        )
                        try:
                            if hasattr(self.notifier, "notify"):
                                await self.notifier.notify(
                                    title=f"🚨 ANOMALIA: {headline}",
                                    body=snippet,
                                    level="warning" if detection["severity"] == "MEDIA" else "error"
                                )
                            elif hasattr(self.notifier, "send_message"):
                                await self.notifier.send_message(msg)
                            logger.info("ofx_anomaly_notified", fitid=fitid, score=detection["score"])
                        except Exception as notif_err:
                            logger.warning("ofx_anomaly_notif_failed", fitid=fitid, error=str(notif_err))

                anomalies.append({
                    "alert_id": alert_id or (existing[0]["id"] if existing else None),
                    "transacao_id": tx_id,
                    "fitid": fitid,
                    "descricao": t.get("descricao"),
                    "valor": float(t.get("valor", 0.0)),
                    "tipo": t.get("tipo"),
                    "data": t.get("data"),
                    "origem": t.get("origem"),
                    "ticker": ticker,
                    "score": detection["score"],
                    "threshold": detection["threshold"],
                    "severity": detection["severity"],
                    "reason": detection["reason"],
                    "is_new": not ja_registrado
                })

        return anomalies

    def dismiss_alert(self, alert_id: int) -> bool:
        """Marca o alerta como arquivado/reconhecido."""
        return self.repo.dismiss_thesis_alert(alert_id)
