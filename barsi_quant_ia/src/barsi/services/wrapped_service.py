"""Serviço de Retrospectiva Anual (Barsi Wrapped).

Compila métricas anuais de proventos reais, efeito bola de neve,
evolução patrimonial, alocação setorial B.E.S.T. e badges de conquistas.
"""

from __future__ import annotations

import calendar
from datetime import datetime
from typing import Any, Dict, List, Optional

from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position
from barsi.domain.quant_ai import StatisticalBarsiEngine
from barsi.infra.logging import logger

MESES_PT = [
    ("01", "Janeiro", "Jan"),
    ("02", "Fevereiro", "Fev"),
    ("03", "Março", "Mar"),
    ("04", "Abril", "Abr"),
    ("05", "Maio", "Mai"),
    ("06", "Junho", "Jun"),
    ("07", "Julho", "Jul"),
    ("08", "Agosto", "Ago"),
    ("09", "Setembro", "Set"),
    ("10", "Outubro", "Out"),
    ("11", "Novembro", "Nov"),
    ("12", "Dezembro", "Dez"),
]

SETORES_BEST = {"BANCOS", "ENERGIA", "SANEAMENTO", "SEGUROS", "TELECOM"}


class WrappedService:
    """Calcula e formata a retrospectiva anual no padrão Barsi."""

    @staticmethod
    def format_brl(val: float) -> str:
        """Formata valor monetário no padrão brasileiro (R$ 1.500,00)."""
        return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    @classmethod
    def get_annual_retrospective(
        cls,
        year: int,
        repo: BudgetRepository,
        positions: Optional[List[Position]] = None,
    ) -> Dict[str, Any]:
        """Gera o consolidado anual completo para o ano informado."""
        if not (2000 <= year <= 2100):
            raise ValueError(f"Ano inválido para retrospectiva: {year}")

        # 1. Proventos reais recebidos no ano
        dividends = repo.list_dividends_received(year=year, limit=2000)

        # Caso dividends_received esteja vazio para o ano, buscamos transações OFX com categoria Proventos/Dividendos
        if not dividends:
            try:
                # Busca transações do ano todo se houver categoria Proventos
                with repo._conn() as c:
                    rows = c.execute(
                        """
                        SELECT fitid, data, mes_ano, valor, descricao, categoria, ticker
                        FROM transacoes
                        WHERE mes_ano LIKE ? AND (
                            UPPER(categoria) LIKE '%PROVENT%' OR
                            UPPER(categoria) LIKE '%DIVIDEND%' OR
                            UPPER(categoria) LIKE '%JCP%' OR
                            UPPER(categoria) LIKE '%RENDIMENT%' OR
                            UPPER(categoria) LIKE '%JUROS%' OR
                            UPPER(descricao) LIKE '%PROVENT%' OR
                            UPPER(descricao) LIKE '%DIVIDEND%' OR
                            UPPER(descricao) LIKE '%JCP%' OR
                            UPPER(descricao) LIKE '%RENDIMENT%' OR
                            UPPER(descricao) LIKE '%JUROS%'
                        )
                        ORDER BY data ASC
                        """,
                        (f"{year}-%",),
                    ).fetchall()
                    for r in rows:
                        dividends.append({
                            "ticker": r["ticker"] or "DIVERSOS",
                            "data_pagamento": r["data"],
                            "mes_ano": r["mes_ano"],
                            "valor_total": abs(float(r["valor"])),
                            "tipo": "DIVIDENDO",
                            "fonte": "ofx",
                        })
            except Exception as e:
                logger.warning("wrapped_fallback_tx_failed", error=str(e))

        total_proventos = sum(float(d.get("valor_total", 0.0)) for d in dividends)
        media_mensal = total_proventos / 12.0

        # Agrupamento mensal (Jan-Dez)
        meses_map = {m[0]: {"mes": m[0], "nome": m[1], "sigla": m[2], "total": 0.0, "lancamentos": 0} for m in MESES_PT}
        for d in dividends:
            mes_ano = str(d.get("mes_ano", ""))
            if len(mes_ano) >= 7 and mes_ano.startswith(f"{year}-"):
                m_num = mes_ano[5:7]
                if m_num in meses_map:
                    meses_map[m_num]["total"] += float(d.get("valor_total", 0.0))
                    meses_map[m_num]["lancamentos"] += 1

        meses_lista = [meses_map[m[0]] for m in MESES_PT]
        meses_com_renda = sum(1 for m in meses_lista if m["total"] > 0)

        # Melhor mês do ano
        melhor_mes = max(meses_lista, key=lambda x: x["total"]) if total_proventos > 0 else None

        # 2. Ranking de ativos por proventos
        if positions is None:
            try:
                positions = repo.load_positions()
            except Exception:
                positions = []

        pos_map: Dict[str, Position] = {p.ticker.upper(): p for p in positions}

        ticker_totals: Dict[str, Dict[str, Any]] = {}
        maior_lancamento = None

        for d in dividends:
            t = (d.get("ticker") or "OUTROS").strip().upper()
            val = float(d.get("valor_total", 0.0))
            if t not in ticker_totals:
                pos = pos_map.get(t)
                setor = cls._classificar_setor(t, pos)
                empresa = pos.empresa if pos else t
                ticker_totals[t] = {
                    "ticker": t,
                    "empresa": empresa,
                    "setor": setor,
                    "is_best": setor in SETORES_BEST,
                    "total": 0.0,
                    "lancamentos": 0,
                    "preco_atual": pos.preco_atual if pos else 0.0,
                    "custo_medio": pos.custo_medio if pos else 0.0,
                }
            ticker_totals[t]["total"] += val
            ticker_totals[t]["lancamentos"] += 1

            if maior_lancamento is None or val > maior_lancamento["valor"]:
                maior_lancamento = {
                    "ticker": t,
                    "valor": val,
                    "data": d.get("data_pagamento"),
                }

        ranking_ativos = sorted(ticker_totals.values(), key=lambda x: x["total"], reverse=True)
        for r in ranking_ativos:
            r["percentual"] = round((r["total"] / total_proventos * 100.0), 1) if total_proventos > 0 else 0.0

        campeao = ranking_ativos[0] if ranking_ativos else None

        # 3. Evolução Patrimonial através de Snapshots
        snapshots_raw = repo.get_portfolio_snapshots(limit_months=60)
        snapshots_ano = [
            s for s in snapshots_raw
            if str(s.get("data_snapshot", "")).startswith(f"{year}-")
        ]
        snapshots_ano.sort(key=lambda s: str(s.get("data_snapshot", "")))

        patrimonio_inicial = 0.0
        patrimonio_final = 0.0
        cotas_iniciais = 0
        cotas_finais = 0

        if snapshots_ano:
            patrimonio_inicial = float(snapshots_ano[0].get("patrimonio_total", 0.0))
            patrimonio_final = float(snapshots_ano[-1].get("patrimonio_total", 0.0))
            cotas_iniciais = int(snapshots_ano[0].get("cotas_totais", 0))
            cotas_finais = int(snapshots_ano[-1].get("cotas_totais", 0))
        elif positions:
            # Fallback com posição atual consolidada
            pat_atual = sum((p.qtd_antes or 0) * (p.preco_atual or 0.0) for p in positions)
            patrimonio_inicial = pat_atual
            patrimonio_final = pat_atual
            cotas_finais = sum(p.qtd_antes or 0 for p in positions)
            cotas_iniciais = cotas_finais

        variacao_patrimonio = patrimonio_final - patrimonio_inicial
        variacao_patrimonio_pct = (
            round((variacao_patrimonio / patrimonio_inicial * 100.0), 1)
            if patrimonio_inicial > 0 else 0.0
        )
        variacao_cotas = cotas_finais - cotas_iniciais

        # 4. Alocação B.E.S.T. da Carteira
        patrimonio_best = 0.0
        patrimonio_total_pos = 0.0
        alocacao_setores: Dict[str, float] = {}

        for p in positions:
            setor = cls._classificar_setor(p.ticker, p)
            val = (p.qtd_antes or 0) * (p.preco_atual or 0.0)
            patrimonio_total_pos += val
            alocacao_setores[setor] = alocacao_setores.get(setor, 0.0) + val
            if setor in SETORES_BEST:
                patrimonio_best += val

        pct_best = (
            round((patrimonio_best / patrimonio_total_pos * 100.0), 1)
            if patrimonio_total_pos > 0 else 0.0
        )

        # 5. Efeito Bola de Neve (Snowball Effect)
        # Quantas cotas novas os proventos pagaram por conta própria?
        cotas_reinvestidas = 0
        ticker_referencia = campeao["ticker"] if campeao else "Ações B.E.S.T."
        preco_ref = campeao.get("preco_atual", 0.0) if campeao else 0.0

        if preco_ref and preco_ref > 0:
            cotas_reinvestidas = int(total_proventos // preco_ref)
        elif positions:
            precos = [p.preco_atual for p in positions if p.preco_atual > 0]
            preco_medio = sum(precos) / len(precos) if precos else 30.0
            cotas_reinvestidas = int(total_proventos // preco_medio)
        else:
            cotas_reinvestidas = int(total_proventos // 30.0)

        proventos_por_dia = round(total_proventos / (366.0 if calendar.isleap(year) else 365.0), 2)

        # 6. Badges & Conquistas Barsi
        badges = cls._calcular_badges(
            total_proventos=total_proventos,
            meses_com_renda=meses_com_renda,
            pct_best=pct_best,
            cotas_reinvestidas=cotas_reinvestidas,
            campeao=campeao,
            variacao_patrimonio=variacao_patrimonio,
            melhor_mes=melhor_mes,
        )

        return {
            "year": year,
            "total_proventos": round(total_proventos, 2),
            "media_mensal": round(media_mensal, 2),
            "proventos_por_dia": proventos_por_dia,
            "meses_com_renda": meses_com_renda,
            "meses": meses_lista,
            "melhor_mes": melhor_mes,
            "maior_lancamento": maior_lancamento,
            "ranking_ativos": ranking_ativos[:10],
            "campeao": campeao,
            "patrimonio_inicial": round(patrimonio_inicial, 2),
            "patrimonio_final": round(patrimonio_final, 2),
            "variacao_patrimonio": round(variacao_patrimonio, 2),
            "variacao_patrimonio_pct": variacao_patrimonio_pct,
            "cotas_iniciais": cotas_iniciais,
            "cotas_finais": cotas_finais,
            "variacao_cotas": variacao_cotas,
            "pct_best": pct_best,
            "alocacao_setores": {k: round(v, 2) for k, v in alocacao_setores.items()},
            "bola_de_neve": {
                "cotas_compradas": cotas_reinvestidas,
                "ticker_referencia": ticker_referencia,
                "preco_referencia": round(preco_ref, 2) if preco_ref else None,
            },
            "badges": badges,
        }

    @staticmethod
    def _classificar_setor(ticker: str, pos: Optional[Position]) -> str:
        """Classifica o setor garantindo normalização com o padrão B.E.S.T."""
        if pos and pos.setor:
            s_up = pos.setor.strip().upper()
            if any(b in s_up for b in ["BANCO", "FINANCEIR"]):
                return "BANCOS"
            if any(e in s_up for e in ["ENERG", "ELETRIC"]):
                return "ENERGIA"
            if any(san in s_up for san in ["SANEAM", "AGUA"]):
                return "SANEAMENTO"
            if any(seg in s_up for seg in ["SEGUR"]):
                return "SEGUROS"
            if any(tel in s_up for tel in ["TELECOM", "TELEFON"]):
                return "TELECOM"
            if "FII" in s_up or "IMOBIL" in s_up:
                return "FII"

        # Fallback via heurística de ticker do motor quantitativo
        return StatisticalBarsiEngine.identificar_setor(ticker)

    @classmethod
    def _calcular_badges(
        cls,
        total_proventos: float,
        meses_com_renda: int,
        pct_best: float,
        cotas_reinvestidas: int,
        campeao: Optional[Dict[str, Any]],
        variacao_patrimonio: float,
        melhor_mes: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Gera lista de conquistas e badges com status de desbloqueio."""
        badges = []

        # 1. Fortaleza B.E.S.T.
        b_best_unlocked = pct_best >= 60.0
        badges.append({
            "id": "fortaleza_best",
            "icon": "🏛️",
            "titulo": "Fortaleza B.E.S.T.",
            "descricao": "Mais de 60% da carteira investida nos setores perenes B.E.S.T.",
            "desbloqueado": b_best_unlocked,
            "progresso": f"{pct_best:.1f}% / 60%",
        })

        # 2. Relógio Suíço (Renda Constante)
        b_relogio_unlocked = meses_com_renda >= 10
        badges.append({
            "id": "relogio_suico",
            "icon": "⏱️",
            "titulo": "Relógio Suíço",
            "descricao": "Recebimento de proventos em pelo menos 10 meses do ano.",
            "desbloqueado": b_relogio_unlocked,
            "progresso": f"{meses_com_renda}/10 meses",
        })

        # 3. Mês de Ouro
        pico_mes = melhor_mes["total"] if melhor_mes else 0.0
        b_ouro_unlocked = pico_mes >= 500.0 or (pico_mes > 0.0 and total_proventos >= 1000.0)
        badges.append({
            "id": "mes_de_ouro",
            "icon": "🌟",
            "titulo": "Mês de Ouro",
            "descricao": f"Pico mensal expressivo de renda passiva ({melhor_mes['nome'] if melhor_mes else '—'}).",
            "desbloqueado": b_ouro_unlocked,
            "progresso": cls.format_brl(pico_mes),
        })

        # 4. Bola de Neve Ativada
        b_neve_unlocked = cotas_reinvestidas >= 20
        badges.append({
            "id": "bola_de_neve",
            "icon": "❄️",
            "titulo": "Bola de Neve Ativada",
            "descricao": "Proventos suficientes para comprar mais de 20 novas ações geradoras.",
            "desbloqueado": b_neve_unlocked,
            "progresso": f"{cotas_reinvestidas} cotas",
        })

        # 5. Rei dos Dividendos
        campeao_val = campeao["total"] if campeao else 0.0
        b_rei_unlocked = campeao is not None and campeao_val >= 200.0
        badges.append({
            "id": "rei_dividendos",
            "icon": "👑",
            "titulo": "Rei dos Dividendos",
            "descricao": f"Um ativo gerador com proventos anuais sólidos ({campeao['ticker'] if campeao else '—'}).",
            "desbloqueado": b_rei_unlocked,
            "progresso": f"{campeao['ticker'] if campeao else '—'} ({cls.format_brl(campeao_val)})",
        })

        # 6. Disciplina de Barsi
        b_disciplina_unlocked = variacao_patrimonio >= 0 and total_proventos > 0
        badges.append({
            "id": "disciplina_barsi",
            "icon": "💎",
            "titulo": "Paciência de Barsi",
            "descricao": "Foco firme no acúmulo contínuo de ações sem giro desnecessário.",
            "desbloqueado": b_disciplina_unlocked,
            "progresso": "Patrimônio protegido e crescendo",
        })

        return badges

    @classmethod
    def generate_telegram_card(cls, data: Dict[str, Any]) -> str:
        """Formata os dados do Wrapped em um card Markdown elegante para o Telegram."""
        year = data["year"]
        total = data["total_proventos"]
        media = data["media_mensal"]
        dia = data["proventos_por_dia"]
        campeao = data.get("campeao")
        melhor = data.get("melhor_mes")
        bola = data.get("bola_de_neve", {})
        ticker_ref = bola.get("ticker_referencia", "B.E.S.T.")
        pct_best = data.get("pct_best", 0.0)
        badges = data.get("badges", [])
        unlocked_badges = [b for b in badges if b.get("desbloqueado")]

        card = (
            f"🎁 *Barsi Wrapped {year} — Retrospectiva Anual*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 *Total de Proventos:* {cls.format_brl(total)}\n"
            f"📅 *Média Mensal:* {cls.format_brl(media)}/mês\n"
            f"☀️ *Renda Passiva/Dia:* {cls.format_brl(dia)}/dia\n\n"
        )

        if melhor and melhor.get("total", 0) > 0:
            card += f"🏆 *Melhor Mês:* {melhor['nome']} ({cls.format_brl(melhor['total'])})\n"

        if campeao:
            card += f"👑 *Rei dos Dividendos:* *{campeao['ticker']}* — {cls.format_brl(campeao['total'])} ({campeao['percentual']}% do total)\n"

        card += (
            f"🏛️ *Alocação B.E.S.T.:* {pct_best:.1f}%\n"
            f"❄️ *Bola de Neve:* Proventos pagaram *{bola.get('cotas_compradas', 0)}* novas ações de _{ticker_ref}_\n\n"
        )

        # Top 3 Ações
        top3 = data.get("ranking_ativos", [])[:3]
        if top3:
            card += "*Top Ativos Pagadores:*\n"
            for idx, item in enumerate(top3, 1):
                card += f"  {idx}. *{item['ticker']}*: {cls.format_brl(item['total'])} ({item['percentual']}%)\n"
            card += "\n"

        # Conquistas
        if unlocked_badges:
            badge_str = " ".join(f"{b['icon']} {b['titulo']}" for b in unlocked_badges)
            card += f"🎖️ *Badges Desbloqueados ({len(unlocked_badges)}/{len(badges)}):*\n{badge_str}\n\n"

        card += (
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "💬 _\"Compre ações para ser sócio de empresas perenes e acumule quantidade.\"_ — Luiz Barsi Filho\n"
            "🌐 _Veja a retrospectiva completa no painel web em /wrapped_"
        )

        return card
