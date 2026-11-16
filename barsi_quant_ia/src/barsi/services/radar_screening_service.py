"""Serviço de Screening Automatizado da B3 (Radar Fora e Dentro da Carteira).

Varre ativos líquidos nos setores perenes B.E.S.T. da B3, calculando Preço Teto Barsi
e Margem de Segurança para identificar oportunidades imediatas de aporte.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime

from barsi.domain.rules import calcular_margem_seguranca, calcular_preco_teto
from barsi.domain.quant_ai import StatisticalBarsiEngine
from barsi.infra.logging import logger

TICKERS_RADAR_DEFAULT: List[str] = [
    # Bancos
    "BBAS3", "ITSA4", "SANB11", "BBDC4",
    # Seguros
    "BBSE3", "CXSE3", "PSSA3",
    # Energia Elétrica
    "TAEE11", "TRPL4", "CPLE6", "EGIE3", "CMIG4", "ALUP11", "NEOE3",
    # Saneamento
    "SAPR11", "SBSP3", "CSMG3",
    # Telecomunicações
    "VIVT3", "TIMS3",
    # Papel & Celulose / Commodities Perenes / Indústria
    "KLBN11", "SUZB3", "VALE3", "PETR4", "VBBR3", "RAIZ4", "WEGE3", "FLRY3", "ODPV3"
]


class RadarScreeningService:
    """Executa screening quantitativo de oportunidades da B3 no método Barsi."""

    @classmethod
    async def screen_opportunities(
        cls,
        market_adapter: Any,
        margem_min: float = 20.0,
        tickers: Optional[List[str]] = None,
        canonical_positions: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Varre a lista de tickers da B3 e retorna oportunidades com margem >= margem_min."""
        target_tickers = tickers or TICKERS_RADAR_DEFAULT
        oportunidades: List[Dict[str, Any]] = []

        # Mapa de ativos já monitorados na carteira canônica para reaproveitar div_proj se disponível
        pos_map = {p.ticker: p for p in (canonical_positions or [])}

        for ticker in target_tickers:
            try:
                hist = await market_adapter.get_history(ticker, period="1mo")
                if not hist:
                    continue
                preco_atual = float(hist[-1].close)
                if preco_atual <= 0.01:
                    continue

                pos_existente = pos_map.get(ticker)
                if pos_existente and getattr(pos_existente, "div_proj", 0.0) > 0:
                    div_proj = float(pos_existente.div_proj)
                else:
                    # Estimativa conservadora de provento projetado para radar ampliado (8% yield base de mercado B.E.S.T.)
                    div_proj = preco_atual * 0.08

                setor = (
                    getattr(pos_existente, "setor", None)
                    or StatisticalBarsiEngine.identificar_setor(ticker)
                )
                teto_barsi = calcular_preco_teto(div_proj, preco_atual, ticker=ticker, setor=setor)
                margem = calcular_margem_seguranca(teto_barsi, preco_atual)

                if margem >= margem_min:
                    oportunidades.append({
                        "ticker": ticker,
                        "preco_atual": preco_atual,
                        "preco_teto": teto_barsi,
                        "div_proj": div_proj,
                        "margem_seguranca": margem,
                        "setor": setor,
                        "na_carteira": ticker in pos_map,
                    })
            except Exception as exc:
                logger.debug("radar_screening_ticker_failed", ticker=ticker, error=str(exc))

        # Ordena por maior margem de segurança decrescente
        oportunidades.sort(key=lambda x: x["margem_seguranca"], reverse=True)
        return oportunidades

    @classmethod
    def format_telegram_summary(
        cls,
        oportunidades: List[Dict[str, Any]],
        margem_min: float = 20.0,
        max_items: int = 8,
    ) -> str:
        """Formata o resultado do screening em mensagem elegante para o Telegram."""
        if not oportunidades:
            return (
                f"🎯 *Radar B3 Ampliado*\n\n"
                f"Nenhum ativo com margem de segurança >= {margem_min:.0f}% encontrada no momento."
            )

        linhas = []
        for op in oportunidades[:max_items]:
            tag_carteira = " 💼" if op.get("na_carteira") else ""
            linhas.append(
                f"• *{op['ticker']}*{tag_carteira} ({op['setor']}): "
                f"R$ {op['preco_atual']:.2f} · Teto R$ {op['preco_teto']:.2f} "
                f"(Margem: *+{op['margem_seguranca']:.1f}%*)"
            )

        corpo = "\n".join(linhas)
        rodape = "\n\n💼 _Ativos com 💼 já fazem parte da sua carteira canônica._" if any(op.get("na_carteira") for op in oportunidades[:max_items]) else ""

        return (
            f"🎯 *Radar B3 Ampliado — {len(oportunidades)} Oportunidades >= {margem_min:.0f}%*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{corpo}"
            f"{rodape}"
        )

    @classmethod
    async def execute_and_notify(
        cls,
        market_adapter: Any,
        notifier: Any,
        repo: Any,
        margem_min: float = 20.0,
        canonical_positions: Optional[List[Any]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Executa a rotina de fechamento de mercado e envia notificação se houver oportunidades."""
        # Se não for forçado, valida preferência do usuário no SQLite
        if not force and repo and hasattr(repo, "is_notification_enabled"):
            if not repo.is_notification_enabled("radar_b3"):
                logger.info("radar_b3_notification_disabled_by_user")
                return {"enviado": False, "motivo": "preferencia_desabilitada", "oportunidades": 0}

        oportunidades = await cls.screen_opportunities(
            market_adapter=market_adapter,
            margem_min=margem_min,
            canonical_positions=canonical_positions,
        )

        if not oportunidades:
            logger.info("radar_b3_no_opportunities_found", margem_min=margem_min)
            return {"enviado": False, "motivo": "sem_oportunidades", "oportunidades": 0}

        texto = cls.format_telegram_summary(oportunidades, margem_min=margem_min)
        enviado = False
        if notifier:
            try:
                enviado = await notifier.notify(
                    title=f"Radar B3 Fechamento ({len(oportunidades)} Oportunidades)",
                    body=texto,
                    level="alert",
                )
            except Exception as e:
                logger.error("radar_b3_notification_failed", error=str(e))

        return {
            "enviado": enviado,
            "total_oportunidades": len(oportunidades),
            "oportunidades": oportunidades[:10],
        }
