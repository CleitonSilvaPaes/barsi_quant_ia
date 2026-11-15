"""Serviço de Varredura e Notificação de Alertas de Quebra de Tese (Early Warning).

Varre automaticamente as notícias recentes dos ativos da carteira no Google News RSS,
aplica o classificador NLP léxico-neural de risco e despacha alertas prioritários
para Telegram e WhatsApp.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.early_warning import EarlyWarningDetector, ThesisAlertResult
from barsi.ports.news import NewsPort
from barsi.ports.notifications import NotificationsPort
from barsi.infra.logging import logger

ANOMALY_RISK_TYPE = "ANOMALIA_EXTRATO"


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
        transacoes: Optional[List[Union[Dict[str, Any], Any]]] = None,
        limit: int = 50,
        model_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Avalia transações contra o Isolation Forest e registra/notifica anomalias.

        Aceita dicts (linhas do SQLite), `sqlite3.Row` ou objetos pydantic `Transacao`
        (fluxo de upload OFX). Objetos sem `id` são reconciliados com o SQLite via FITID
        para respeitar o feedback 'OK_ANOMALIA' e permitir arquivamento posterior.
        Em modo fallback (modelo .npz ausente) os alertas são registrados, mas NÃO notificados.
        """
        from barsi.domain.anomaly_detector import AnomalyDetector, to_tx_dict

        detector = AnomalyDetector.get_instance(model_path=model_path)
        raw = transacoes if transacoes is not None else self.repo.list_transacoes(limit=limit)

        anomalies: List[Dict[str, Any]] = []
        for item in raw:
            t = dict(to_tx_dict(item))
            fitid = t.get("fitid") or t.get("id_transacao")
            if not t.get("id") and fitid:
                persisted = self.repo.get_transacao_by_fitid(fitid)
                if persisted:
                    t = {**t, **persisted}

            if t.get("feedback_usuario") == "OK_ANOMALIA":
                continue

            detection = detector.detect(t)
            if not detection["is_anomaly"]:
                continue

            tx_id = t.get("id")
            if tx_id:
                self.repo.update_transacao_anomaly_score(tx_id, detection["score"])

            fitid = fitid or f"id_{tx_id}"
            ticker = str(t.get("ticker") or t.get("ticker_relacionado") or "EXTRATO").strip().upper()
            valor_abs = abs(float(t.get("valor", 0.0) or 0.0))
            headline = f"Transação Incomum: {str(t.get('descricao', ''))[:35]}"
            snippet = (
                f"Valor R$ {valor_abs:,.2f} ({t.get('origem', 'CONTA')}) | "
                f"Score: {detection['score']:.2f} | {detection['reason']}"
            )

            # Deduplicação por FITID entre alertas ativos do mesmo tipo
            existing = self.repo.list_thesis_alerts(ticker=ticker, active_only=True)
            match = next(
                (e for e in existing
                 if e["risk_type"] == ANOMALY_RISK_TYPE and f"[{fitid}]" in (e.get("snippet") or "")),
                None,
            )

            alert_id = match["id"] if match else None
            if match is None:
                alert_id = self.repo.record_thesis_alert(
                    ticker=ticker,
                    risk_type=ANOMALY_RISK_TYPE,
                    severity=detection["severity"],
                    headline=headline,
                    snippet=f"[{fitid}] {snippet}",
                    source="isolation_forest" if not detection.get("fallback") else "heuristic_fallback",
                )
                if self.notifier and not detection.get("fallback"):
                    await self._notify_anomaly(t, detection, headline, snippet, fitid, valor_abs)

            anomalies.append({
                "alert_id": alert_id,
                "transacao_id": tx_id,
                "fitid": fitid,
                "descricao": t.get("descricao"),
                "valor": float(t.get("valor", 0.0) or 0.0),
                "tipo": t.get("tipo"),
                "data": t.get("data"),
                "origem": t.get("origem"),
                "ticker": ticker,
                "is_anomaly": True,
                "score": detection["score"],
                "threshold": detection["threshold"],
                "severity": detection["severity"],
                "reason": detection["reason"],
                "fallback": bool(detection.get("fallback")),
                "is_new": match is None,
            })

        return anomalies

    async def _notify_anomaly(
        self, t: Dict[str, Any], detection: Dict[str, Any], headline: str, snippet: str, fitid: str, valor_abs: float
    ) -> None:
        msg = (
            f"🚨 *ALERTA DE ANOMALIA EM EXTRATO*\n\n"
            f"💳 *Transação:* {t.get('descricao')}\n"
            f"💵 *Valor:* R$ {valor_abs:,.2f} ({t.get('tipo')})\n"
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
                    level="warning" if detection["severity"] == "MEDIA" else "error",
                )
            elif hasattr(self.notifier, "send_message"):
                await self.notifier.send_message(msg)
            logger.info("ofx_anomaly_notified", fitid=fitid, score=detection["score"])
        except Exception as notif_err:  # noqa: BLE001 — notificação é best-effort
            logger.warning("ofx_anomaly_notif_failed", fitid=fitid, error=str(notif_err))

    def dismiss_alert(self, alert_id: int) -> bool:
        """Marca o alerta como arquivado/reconhecido."""
        return self.repo.dismiss_thesis_alert(alert_id)
