"""Serviço de Backtesting e Projeção de Bola de Neve (Longo Prazo).

Simula a evolução da carteira previdenciária em horizontes de 5 a 35 anos:
1. Reinvestimento contínuo de proventos.
2. Identificação exata do ponto de inflexão (Crossing Point), onde os dividendos
   superam o aporte do próprio bolso e a bola de neve se autoalimenta.
3. Comparativo com o aporte seco (sem reinvestimento).
4. Equivalência em salários mínimos de renda passiva mensal.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position
from barsi.domain.rules import atualizar_posicao_quant


class SnowballBacktestService:
    """Simulador e projetor estocástico-composto de bola de neve de dividendos."""

    def __init__(self, budget_repo: BudgetRepository):
        self.repo = budget_repo

    def get_portfolio_baseline(self) -> Dict[str, float]:
        """Calcula as métricas de ponto de partida reais da carteira no SQLite."""
        positions = self.repo.load_positions()
        for p in positions:
            atualizar_posicao_quant(p, p.preco_atual, p.div_proj)

        patrimonio = sum(p.valor_total or ((p.qtd_antes + p.qtd_aporte) * p.preco_atual) for p in positions)
        div_anual = sum((p.div_proj or 0.0) * (p.qtd_antes + p.qtd_aporte) for p in positions)
        dy_medio = (div_anual / patrimonio * 100.0) if patrimonio > 0 else 6.5

        return {
            "patrimonio_inicial": round(patrimonio, 2),
            "provento_anual_inicial": round(div_anual, 2),
            "dy_medio_carteira": round(max(5.0, dy_medio), 2)
        }

    def simulate_snowball(
        self,
        aporte_mensal: float = 1000.0,
        taxa_reinvestimento: float = 1.0,
        anos: int = 20,
        dy_base: Optional[float] = None,
        crescimento_anual_proventos: float = 6.0,
        valor_inicial: Optional[float] = None
    ) -> Dict[str, Any]:
        """Executa a projeção anualizada de bola de neve composta."""
        if aporte_mensal < 0:
            raise ValueError("O aporte mensal não pode ser negativo.")
        if anos < 1 or anos > 50:
            raise ValueError("O período em anos deve estar entre 1 e 50 anos.")

        baseline = self.get_portfolio_baseline()
        p0 = float(valor_inicial if valor_inicial is not None else baseline["patrimonio_inicial"])
        dy = float(dy_base if dy_base is not None else baseline["dy_medio_carteira"])

        taxa_reinv = min(1.0, max(0.0, taxa_reinvestimento))
        taxa_crescimento = max(0.0, crescimento_anual_proventos / 100.0)

        patrimonio = p0
        total_aportado = p0
        patrimonio_sem_reinv = p0
        total_divs_acumulados = 0.0

        curva: List[Dict[str, Any]] = []
        ano_crossover: Optional[int] = None

        ano_corrente = datetime.datetime.now().year

        # Ano 0 (Estado Inicial)
        div_ano_0 = round(patrimonio * (dy / 100.0), 2)
        curva.append({
            "ano": 0,
            "calendario": ano_corrente,
            "patrimonio": round(patrimonio, 2),
            "sem_reinvestimento": round(patrimonio_sem_reinv, 2),
            "total_aportado_bolso": round(total_aportado, 2),
            "provento_anual": div_ano_0,
            "provento_mensal": round(div_ano_0 / 12.0, 2),
            "aporte_ano": 0.0,
            "crossover": False
        })

        aporte_anual = round(aporte_mensal * 12.0, 2)

        for t in range(1, anos + 1):
            total_aportado += aporte_anual

            # O DY cresce ao longo dos anos devido ao crescimento orgânico dos lucros das empresas B.E.S.T.
            dy_t = dy * ((1.0 + (taxa_crescimento * 0.5)) ** (t - 1))
            provento_ano = round(patrimonio * (dy_t / 100.0), 2)
            total_divs_acumulados += provento_ano

            reinvestimento = round(provento_ano * taxa_reinv, 2)

            # Valorização moderada do capital das cotas recompondo a inflação
            valorizacao = round(patrimonio * (taxa_crescimento * 0.5), 2)

            patrimonio = round(patrimonio + aporte_anual + reinvestimento + valorizacao, 2)
            patrimonio_sem_reinv = round(patrimonio_sem_reinv + aporte_anual + (patrimonio_sem_reinv * (taxa_crescimento * 0.5)), 2)

            is_crossover = (provento_ano >= aporte_anual and aporte_anual > 0)
            if is_crossover and ano_crossover is None:
                ano_crossover = t

            provento_mensal_t = round((patrimonio * (dy_t / 100.0)) / 12.0, 2)

            curva.append({
                "ano": t,
                "calendario": ano_corrente + t,
                "patrimonio": patrimonio,
                "sem_reinvestimento": patrimonio_sem_reinv,
                "total_aportado_bolso": total_aportado,
                "provento_anual": provento_ano,
                "provento_mensal": provento_mensal_t,
                "aporte_ano": aporte_anual,
                "crossover": is_crossover
            })

        renda_mensal_final = curva[-1]["provento_mensal"]
        salario_minimo_ref = 1518.0
        salarios_minimos = round(renda_mensal_final / salario_minimo_ref, 1)
        multiplicador = round(patrimonio / (total_aportado or 1.0), 2)

        return {
            "parametros": {
                "aporte_mensal": aporte_mensal,
                "taxa_reinvestimento_pct": round(taxa_reinv * 100.0, 1),
                "anos": anos,
                "dy_base_pct": dy,
                "crescimento_anual_pct": crescimento_anual_proventos,
                "patrimonio_inicial": p0
            },
            "resumo": {
                "patrimonio_final": patrimonio,
                "patrimonio_sem_reinvestimento": patrimonio_sem_reinv,
                "vantagem_drip_reinvestimento": round(patrimonio - patrimonio_sem_reinv, 2),
                "total_aportado_bolso": total_aportado,
                "total_dividendos_gerados": round(total_divs_acumulados, 2),
                "renda_mensal_final": renda_mensal_final,
                "equivalente_salarios_minimos": salarios_minimos,
                "ano_crossover": ano_crossover,
                "multiplicador_patrimonial": multiplicador
            },
            "curva": curva
        }

