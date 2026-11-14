"""Serviço de Reinvestimento Automático de Proventos (DRIP Barsi - Dividend Reinvestment Plan).

Acelera o efeito da bola de neve de juros compostos segundo a metodologia Luiz Barsi:
1. Reinvestimento integral dos proventos recebidos.
2. Filtro estrito de Margem de Segurança (somente ativos cotados abaixo do Preço Teto).
3. Rebalanceamento defensivo dos setores perenes B.E.S.T.
4. Alocação em lotes inteiros executáveis, minimizando o troco residual.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position, AcaoBarsi
from barsi.domain.rules import atualizar_posicao_quant, is_fii
from barsi.infra.logging import logger


class DripService:
    """Motor quantitativo de sugestão e execução de reinvestimento de dividendos."""

    def __init__(self, budget_repo: BudgetRepository):
        self.repo = budget_repo

    def get_available_dividends_for_month(self, mes_ano: Optional[str] = None) -> float:
        """Obtém o montante total de dividendos recebidos no mês especificado (ou mês atual)."""
        target = mes_ano or datetime.datetime.now().strftime("%Y-%m")
        divs = self.repo.list_dividends_received(mes_ano=target)
        return round(sum(d.get("valor_total", 0.0) for d in divs), 2)

    def simulate_drip(
        self,
        valor_disponivel: Optional[float] = None,
        mes_ano: Optional[str] = None,
        balance_sectors: bool = True,
        max_ativos: int = 4
    ) -> Dict[str, Any]:
        """Calcula a alocação ótima de reinvestimento de proventos.

        Se `valor_disponivel` não for fornecido ou for <= 0, puxa automaticamente
        o total de dividendos registrados no mês em `dividends_received`.
        """
        target_month = mes_ano or datetime.datetime.now().strftime("%Y-%m")

        if valor_disponivel is None or valor_disponivel <= 0:
            valor_disponivel = self.get_available_dividends_for_month(target_month)

        if valor_disponivel <= 0:
            # Fallback: se o mês atual estiver zerado, tenta obter a média dos últimos proventos
            recent_divs = self.repo.list_dividends_received(limit=10)
            if recent_divs:
                valor_disponivel = round(sum(d.get("valor_total", 0.0) for d in recent_divs[:3]), 2)

        if valor_disponivel <= 0:
            valor_disponivel = 500.0  # Simulação base padrão caso não haja registros

        positions = self.repo.load_positions()
        if not positions:
            return {
                "valor_disponivel": valor_disponivel,
                "valor_alocado": 0.0,
                "troco_residual": valor_disponivel,
                "provento_anual_adicional": 0.0,
                "provento_mensal_adicional": 0.0,
                "yield_medio_adicional_pct": 0.0,
                "ordens": [],
                "aviso": "Nenhuma posição cadastrada na carteira para reinvestimento."
            }

        # Atualiza métricas quantitativas de cada posição
        for p in positions:
            atualizar_posicao_quant(p, p.preco_atual, p.div_proj)

        # 1. Mapeamento da distribuição setorial atual
        patrimonio_total = sum(p.valor_total or ((p.qtd_antes + p.qtd_aporte) * p.preco_atual) for p in positions)
        setor_patrimonio: Dict[str, float] = {}
        for p in positions:
            s = p.setor or "Outros"
            val = p.valor_total or ((p.qtd_antes + p.qtd_aporte) * p.preco_atual)
            setor_patrimonio[s] = setor_patrimonio.get(s, 0.0) + val

        # 2. Filtragem de candidatos elegíveis segundo a metodologia Barsi
        candidatos: List[Position] = []
        for p in positions:
            if p.preco_atual <= 0 or p.div_proj <= 0:
                continue
            # Critério Barsi: cotação deve estar abaixo ou igual ao Preço Teto
            if p.margem_seguranca >= 0 and p.status_barsi in (AcaoBarsi.ACUMULAR, AcaoBarsi.MANTER):
                candidatos.append(p)

        if not candidatos:
            # Se nenhum estiver estritamente abaixo do teto, pega os com menor margem negativa (mais próximos)
            candidatos = sorted([p for p in positions if p.preco_atual > 0 and p.div_proj > 0],
                                key=lambda x: x.margem_seguranca, reverse=True)[:max_ativos]

        # 3. Cálculo do Score de Prioridade DRIP
        scores: List[Dict[str, Any]] = []
        for p in candidatos:
            dy_proj = (p.div_proj / p.preco_atual) * 100.0 if p.preco_atual > 0 else 0.0
            margem_score = min(100.0, max(0.0, p.margem_seguranca))
            dy_score = min(100.0, max(0.0, dy_proj * 10.0))  # 10% DY = 100 pts

            # Fator de rebalanceamento setorial B.E.S.T. (setores com menos exposição ganham bônus)
            peso_setor = (setor_patrimonio.get(p.setor, 0.0) / (patrimonio_total or 1.0))
            bonus_setor = max(0.0, (0.35 - peso_setor) * 100.0) if balance_sectors else 0.0

            # Score ponderado: 40% Margem, 40% Yield, 20% Rebalanceamento Setorial
            score_final = (margem_score * 0.40) + (dy_score * 0.40) + (bonus_setor * 0.20)

            scores.append({
                "posicao": p,
                "score": round(score_final, 2),
                "dy_proj": round(dy_proj, 2),
                "peso_setor_pct": round(peso_setor * 100.0, 1),
                "is_fii": is_fii(p.ticker, p.setor)
            })

        # Ordena pelo score ponderado
        scores.sort(key=lambda x: x["score"], reverse=True)
        top_candidatos = scores[:max_ativos]

        # 4. Distribuição do saldo em lotes inteiros executáveis
        saldo = float(valor_disponivel)
        ordens: List[Dict[str, Any]] = []

        # Distribui proporcionalmente ou iterativamente
        orcamento_por_ativo = saldo / len(top_candidatos) if top_candidatos else 0.0

        for item in top_candidatos:
            p = item["posicao"]
            if p.preco_atual <= 0 or saldo < p.preco_atual:
                continue

            # Quantidade alvo estimada pela fatia do orçamento
            qtd_ideal = int(orcamento_por_ativo / p.preco_atual)
            if qtd_ideal == 0 and saldo >= p.preco_atual:
                qtd_ideal = 1

            # Não gasta mais do que o saldo restante
            qtd_efetiva = min(qtd_ideal, int(saldo / p.preco_atual))
            if qtd_efetiva <= 0:
                continue

            gasto = round(qtd_efetiva * p.preco_atual, 2)
            saldo = round(saldo - gasto, 2)
            provento_anual_inc = round(qtd_efetiva * p.div_proj, 2)

            ordens.append({
                "ticker": p.ticker,
                "empresa": p.empresa,
                "setor": p.setor,
                "preco_atual": round(p.preco_atual, 2),
                "preco_teto": round(p.preco_teto, 2),
                "margem_seguranca": round(p.margem_seguranca, 1),
                "dy_proj": item["dy_proj"],
                "qtd_sugerida": qtd_efetiva,
                "valor_gasto": gasto,
                "provento_anual_inc": provento_anual_inc,
                "provento_mensal_inc": round(provento_anual_inc / 12.0, 2),
                "motivo": f"Margem de {p.margem_seguranca:.1f}% e DY de {item['dy_proj']:.1f}% no setor {p.setor}."
            })

        # Aloca troco residual no ativo de maior score caso ainda haja saldo suficiente para ao menos 1 cota
        if saldo > 0 and ordens:
            for ord in ordens:
                preco = ord["preco_atual"]
                if saldo >= preco:
                    cotas_extra = int(saldo / preco)
                    if cotas_extra > 0:
                        ord["qtd_sugerida"] += cotas_extra
                        gasto_extra = round(cotas_extra * preco, 2)
                        ord["valor_gasto"] = round(ord["valor_gasto"] + gasto_extra, 2)
                        p_pos = next((c["posicao"] for c in top_candidatos if c["posicao"].ticker == ord["ticker"]), None)
                        if p_pos:
                            inc_extra = round(cotas_extra * p_pos.div_proj, 2)
                            ord["provento_anual_inc"] = round(ord["provento_anual_inc"] + inc_extra, 2)
                            ord["provento_mensal_inc"] = round(ord["provento_anual_inc"] / 12.0, 2)
                        saldo = round(saldo - gasto_extra, 2)
                        if saldo <= 0:
                            break

        total_alocado = round(valor_disponivel - saldo, 2)
        total_provento_anual = round(sum(o["provento_anual_inc"] for o in ordens), 2)
        total_provento_mensal = round(total_provento_anual / 12.0, 2)
        yield_adicional = round((total_provento_anual / total_alocado) * 100.0, 2) if total_alocado > 0 else 0.0

        return {
            "valor_disponivel": round(valor_disponivel, 2),
            "valor_alocado": total_alocado,
            "troco_residual": round(saldo, 2),
            "provento_anual_adicional": total_provento_anual,
            "provento_mensal_adicional": total_provento_mensal,
            "yield_medio_adicional_pct": yield_adicional,
            "mes_referencia": target_month,
            "ordens": ordens
        }

    def apply_drip_orders(self, orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Aplica as ordens sugeridas pelo DRIP diretamente no SQLite canônico, incrementando qtd_aporte."""
        if not orders:
            return {"status": "ok", "aplicadas": 0, "message": "Nenhuma ordem para aplicar."}

        positions = self.repo.load_positions()
        pos_by_ticker = {p.ticker.upper(): p for p in positions}

        aplicadas = 0
        total_reinvestido = 0.0

        for ord in orders:
            ticker = ord.get("ticker", "").upper()
            qtd = int(ord.get("qtd_sugerida", 0))
            if not ticker or qtd <= 0:
                continue

            pos = pos_by_ticker.get(ticker)
            if pos:
                pos.qtd_aporte = (pos.qtd_aporte or 0) + qtd
                pos.qtd_total = pos.qtd_antes + pos.qtd_aporte
                pos.total_aporte = round(pos.qtd_aporte * pos.preco_atual, 2)
                pos.valor_total = round(pos.qtd_total * pos.preco_atual, 2)
                aplicadas += 1
                total_reinvestido += float(ord.get("valor_gasto", qtd * pos.preco_atual))

        if aplicadas > 0:
            self.repo.save_positions(list(pos_by_ticker.values()))
            logger.info("drip_orders_applied", aplicadas=aplicadas, total_reinvestido=total_reinvestido)

        return {
            "status": "ok",
            "aplicadas": aplicadas,
            "total_reinvestido": round(total_reinvestido, 2),
            "message": f"{aplicadas} ordens de reinvestimento aplicadas à carteira com sucesso."
        }
