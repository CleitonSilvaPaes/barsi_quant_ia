"""Serviço de Rebalanceamento Setorial Inteligente e Alerta de Concentração B.E.S.T.

Monitora a exposição percentual em cada setor da carteira, alertando sobre
hiperconcentração (> 35%) e recomendando redirecionamento de aportes para setores
sub-representados nos pilares B.E.S.T. (Bancos, Energia, Saneamento, Telecom, Seguros).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime

from barsi.domain.models import Position
from barsi.domain.quant_ai import StatisticalBarsiEngine
from barsi.infra.logging import logger

SETORES_BEST_OFICIAIS = ["BANCOS", "ENERGIA", "SANEAMENTO", "SEGUROS", "TELECOM"]
TETO_CONCENTRACAO_DEFAULT = 35.0  # Máximo de 35% de exposição por setor


class PortfolioBalanceService:
    """Calcula diagnóstico setorial e recomendações de rebalanceamento passivo por novos aportes."""

    @classmethod
    def analyze_sector_allocation(
        cls,
        positions: List[Position],
        max_concentration: float = TETO_CONCENTRACAO_DEFAULT,
    ) -> Dict[str, Any]:
        """Calcula distribuição setorial detalhada, desvios e oportunidades de rebalanceamento."""
        if not positions:
            return {
                "patrimonio_total": 0.0,
                "setores": {},
                "concentracao_maxima": max_concentration,
                "setores_concentrados": [],
                "setores_sub_alocados": [],
                "tem_alerta": False,
                "recomendacao": "Carteira vazia.",
            }

        patrimonio_total = 0.0
        setores_dados: Dict[str, Dict[str, Any]] = {}

        # 1. Agregação de valores por setor
        for pos in positions:
            qtd = float(pos.qtd_antes or 0)
            preco = float(pos.preco_atual or 0.0)
            valor_ativo = qtd * preco
            patrimonio_total += valor_ativo

            # Normalização canônica do setor garantindo padrão B.E.S.T.
            setor_nome = StatisticalBarsiEngine.normalize_sector(pos.setor) if pos.setor else "OUTROS"
            if setor_nome == "OUTROS":
                setor_nome = StatisticalBarsiEngine.identificar_setor(pos.ticker)

            if setor_nome not in setores_dados:
                setores_dados[setor_nome] = {
                    "setor": setor_nome,
                    "valor_total": 0.0,
                    "percentual": 0.0,
                    "ativos": [],
                    "is_best": setor_nome.upper() in SETORES_BEST_OFICIAIS,
                }

            setores_dados[setor_nome]["valor_total"] += valor_ativo
            setores_dados[setor_nome]["ativos"].append({
                "ticker": pos.ticker,
                "valor": valor_ativo,
                "preco_atual": preco,
                "status": pos.status_barsi.value if hasattr(pos.status_barsi, "value") else str(pos.status_barsi),
                "margem": float(getattr(pos, "margem_seguranca", 0.0) or 0.0),
            })

        # 2. Cálculo dos percentuais
        if patrimonio_total > 0:
            for s in setores_dados.values():
                s["percentual"] = round((s["valor_total"] / patrimonio_total) * 100.0, 1)

        # 3. Classificação de concentração e sub-alocação
        setores_ordenados = sorted(
            setores_dados.values(),
            key=lambda x: x["valor_total"],
            reverse=True,
        )

        setores_concentrados = [
            s for s in setores_ordenados if s["percentual"] > max_concentration
        ]

        # Setores B.E.S.T. presentes ou faltantes que têm menor representação (< 15% ou ausentes)
        setores_sub_alocados = [
            s for s in setores_ordenados if s["is_best"] and s["percentual"] < 15.0
        ]
        setores_presentes_nomes = {s["setor"].upper() for s in setores_ordenados}
        setores_faltantes_best = [s for s in SETORES_BEST_OFICIAIS if s not in setores_presentes_nomes]

        tem_alerta = len(setores_concentrados) > 0

        # 4. Síntese de recomendação tática
        if tem_alerta:
            top_conc = setores_concentrados[0]
            sugestoes_destinos = []
            if setores_sub_alocados:
                sugestoes_destinos.extend([s["setor"] for s in setores_sub_alocados[:2]])
            if setores_faltantes_best:
                sugestoes_destinos.extend(setores_faltantes_best[:2])
            destinos_str = ", ".join(sugestoes_destinos) or "outros setores B.E.S.T."
            recomendacao = (
                f"Concentração elevada em {top_conc['setor']} ({top_conc['percentual']}%). "
                f"Pausa técnica em compras desse setor: direcione os próximos aportes prioritariamente para {destinos_str}."
            )
        else:
            recomendacao = "Alocação setorial bem equilibrada e em conformidade com as diretrizes B.E.S.T."

        return {
            "patrimonio_total": round(patrimonio_total, 2),
            "concentracao_maxima": max_concentration,
            "setores": setores_ordenados,
            "setores_concentrados": setores_concentrados,
            "setores_sub_alocados": setores_sub_alocados,
            "setores_faltantes_best": setores_faltantes_best,
            "tem_alerta": tem_alerta,
            "recomendacao": recomendacao,
        }

    @classmethod
    def format_telegram_report(
        cls,
        analysis: Dict[str, Any],
    ) -> str:
        """Formata relatório de alocação setorial para o Telegram."""
        patrimonio = analysis.get("patrimonio_total", 0.0)
        setores = analysis.get("setores", [])
        tem_alerta = analysis.get("tem_alerta", False)
        limite = analysis.get("concentracao_maxima", TETO_CONCENTRACAO_DEFAULT)

        if not setores:
            return "💼 *Rebalanceamento Setorial*: Carteira vazia ou sem posições cadastradas."

        linhas = []
        for s in setores:
            pct = s["percentual"]
            barra = "🟥" if pct > limite else ("🟩" if s["is_best"] else "🟦")
            aviso_alerta = " ⚠️ *(Alerta)*" if pct > limite else ""
            linhas.append(
                f"{barra} *{s['setor']}*: {pct:.1f}% (R$ {s['valor_total']:,.2f}){aviso_alerta}"
            )

        corpo_setores = "\n".join(linhas)

        cabecalho = "⚖️ *Diagnóstico Setorial & Concentração B.E.S.T.*"
        if tem_alerta:
            cabecalho += "\n⚠️ *Atenção: Concentração acima do teto de segurança!*"

        msg = (
            f"{cabecalho}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 *Patrimônio Analisado:* R$ {patrimonio:,.2f}\n"
            f"🎯 *Teto Prudencial por Setor:* {limite:.0f}%\n\n"
            f"*Distribuição Atual:*\n"
            f"{corpo_setores}\n\n"
            f"💡 *Diretriz Barsi:*\n"
            f"_{analysis.get('recomendacao')}_"
        )
        return msg

    @classmethod
    async def check_and_notify_imbalance(
        cls,
        positions: List[Position],
        notifier: Any,
        repo: Any,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Verifica se há hiperconcentração e dispara alerta se habilitado."""
        if not force and repo and hasattr(repo, "is_notification_enabled"):
            if not repo.is_notification_enabled("rebalanceamento_setorial"):
                return {"alerta_enviado": False, "motivo": "preferencia_desabilitada"}

        analysis = cls.analyze_sector_allocation(positions)
        if not analysis.get("tem_alerta"):
            return {"alerta_enviado": False, "motivo": "sem_desbalanceamento"}

        msg = cls.format_telegram_report(analysis)
        enviado = False
        if notifier:
            try:
                enviado = await notifier.notify(
                    title="Alerta de Concentração Setorial",
                    body=msg,
                    level="warn",
                )
            except Exception as e:
                logger.error("sector_imbalance_notification_failed", error=str(e))

        return {
            "alerta_enviado": enviado,
            "setores_concentrados": [s["setor"] for s in analysis.get("setores_concentrados", [])],
        }

