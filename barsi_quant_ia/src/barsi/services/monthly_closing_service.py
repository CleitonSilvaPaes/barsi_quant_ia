"""Serviço de Fechamento Mensal Automatizado (Relatório Executivo PDF).

Gera um dossiê executivo mensal consolidado para o investidor da metodologia Barsi:
- Patrimônio Total & Cotas
- Proventos Recebidos no Mês & Evolução da Bola de Neve
- Balanço Orçamentário (Receitas, Despesas Fixas/Variáveis, Capacidade de Aporte)
- Distribuição Setorial B.E.S.T.
- Plano de Aportes Prioritários para o próximo ciclo
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position
from barsi.domain.rules import atualizar_posicao_quant
from barsi.infra.logging import logger


class MonthlyClosingService:
    """Consolida os dados mensais e renderiza relatórios executivos em HTML e PDF."""

    def __init__(self, budget_repo: BudgetRepository):
        self.repo = budget_repo

    def get_closing_data(self, mes_ano: Optional[str] = None) -> Dict[str, Any]:
        """Extrai todos os dados analíticos necessários para o fechamento mensal."""
        target_month = mes_ano or datetime.datetime.now().strftime("%Y-%m")
        now_dt = datetime.datetime.now()

        # 1. Posições e Carteira
        positions = self.repo.load_positions()
        for p in positions:
            atualizar_posicao_quant(p, p.preco_atual, p.div_proj)

        patrimonio_total = sum(p.valor_total or ((p.qtd_antes + p.qtd_aporte) * p.preco_atual) for p in positions)
        total_cotas = sum(p.qtd_antes + p.qtd_aporte for p in positions)
        proj_div_anual = sum((p.div_proj or 0.0) * (p.qtd_antes + p.qtd_aporte) for p in positions)
        proj_div_mensal = round(proj_div_anual / 12.0, 2)

        # Yield on Cost Médio Ponderado
        custo_total = sum((getattr(p, "custo_medio", 0.0) or p.preco_atual) * (p.qtd_antes + p.qtd_aporte) for p in positions)
        yoc_medio = round((proj_div_anual / custo_total) * 100.0, 2) if custo_total > 0 else 0.0

        # Distribuição Setorial
        setores_totais: Dict[str, float] = {}
        for p in positions:
            s = p.setor or "Outros"
            val = p.valor_total or ((p.qtd_antes + p.qtd_aporte) * p.preco_atual)
            setores_totais[s] = setores_totais.get(s, 0.0) + val

        setores_dist: List[Dict[str, Any]] = []
        for s, v in sorted(setores_totais.items(), key=lambda x: x[1], reverse=True):
            pct = round((v / (patrimonio_total or 1.0)) * 100.0, 1)
            setores_dist.append({"setor": s, "valor": round(v, 2), "pct": pct})

        # 2. Dividendos Recebidos no Mês e no Ano
        divs_mes = self.repo.list_dividends_received(mes_ano=target_month)
        total_divs_mes = round(sum(d.get("valor_total", 0.0) for d in divs_mes), 2)

        current_year = int(target_month.split("-")[0])
        total_divs_ano = round(self.repo.get_total_dividends_received(year=current_year), 2)

        # 3. Balanço Orçamentário do Mês via transações
        with self.repo._conn() as conn:
            # Entradas do mês
            in_row = conn.execute(
                """
                SELECT COALESCE(SUM(valor), 0.0) as total
                FROM transacoes
                WHERE mes_ano = ? AND tipo = 'CREDIT'
                """,
                (target_month,)
            ).fetchone()
            total_entradas = float(in_row["total"] or 0.0)

            # Despesas do mês por categoria
            desp_rows = conn.execute(
                """
                SELECT c.kind, COALESCE(SUM(t.valor), 0.0) as total
                FROM transacoes t
                LEFT JOIN expense_categories c ON t.categoria_id = c.id
                WHERE t.mes_ano = ? AND t.tipo = 'DEBIT'
                GROUP BY c.kind
                """,
                (target_month,)
            ).fetchall()

            desp_by_kind = {r["kind"] or "variavel": float(r["total"]) for r in desp_rows}
            desp_fixa = desp_by_kind.get("fixa", 0.0)
            desp_variavel = desp_by_kind.get("variavel", 0.0)
            tributos = desp_by_kind.get("tributo", 0.0)
            investimentos_feitos = desp_by_kind.get("investimento", 0.0)
            total_despesas = desp_fixa + desp_variavel + tributos

        poupanca_gerada = round(max(0.0, total_entradas - total_despesas), 2)
        taxa_poupanca = round((poupanca_gerada / total_entradas) * 100.0, 1) if total_entradas > 0 else 0.0

        # 4. Top Oportunidades do Radar B.E.S.T. para o Próximo Mês
        oportunidades = sorted(
            [p for p in positions if p.preco_atual > 0 and p.preco_teto > 0],
            key=lambda x: x.margem_seguranca,
            reverse=True
        )[:5]

        radar_items = []
        for p in oportunidades:
            dy = (p.div_proj / p.preco_atual) * 100.0 if p.preco_atual > 0 else 0.0
            radar_items.append({
                "ticker": p.ticker,
                "empresa": p.empresa,
                "setor": p.setor,
                "preco_atual": round(p.preco_atual, 2),
                "preco_teto": round(p.preco_teto, 2),
                "margem": round(p.margem_seguranca, 1),
                "dy_proj": round(dy, 2),
                "status": p.status_barsi.value
            })

        return {
            "mes_ano": target_month,
            "data_emissao": now_dt.strftime("%d/%m/%Y às %H:%M"),
            "patrimonio_total": round(patrimonio_total, 2),
            "total_cotas": total_cotas,
            "proj_div_mensal": proj_div_mensal,
            "proj_div_anual": round(proj_div_anual, 2),
            "yoc_medio": yoc_medio,
            "total_divs_mes": total_divs_mes,
            "total_divs_ano": total_divs_ano,
            "itens_dividendos": divs_mes,
            "total_entradas": round(total_entradas, 2),
            "desp_fixa": round(desp_fixa, 2),
            "desp_variavel": round(desp_variavel, 2),
            "tributos": round(tributos, 2),
            "total_despesas": round(total_despesas, 2),
            "investimentos_feitos": round(investimentos_feitos, 2),
            "poupanca_gerada": poupanca_gerada,
            "taxa_poupanca_pct": taxa_poupanca,
            "setores_dist": setores_dist,
            "radar_oportunidades": radar_items
        }

    def generate_html_report(self, mes_ano: Optional[str] = None) -> str:
        """Gera o HTML profissional e auto-contido do relatório executivo, formatado para impressão A4."""
        d = self.get_closing_data(mes_ano)

        def fmt_brl(v: float) -> str:
            return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        # Linhas de dividendos
        div_rows_html = ""
        if d["itens_dividendos"]:
            for item in d["itens_dividendos"]:
                div_rows_html += f"""
                <tr>
                    <td><strong>{item.get('ticker', '-')}</strong></td>
                    <td>{item.get('data_pagamento', '-')}</td>
                    <td>{item.get('tipo', 'DIVIDENDO')}</td>
                    <td style="text-align: right; font-weight: 600; color: #16a34a;">{fmt_brl(float(item.get('valor_total', 0.0)))}</td>
                </tr>
                """
        else:
            div_rows_html = f"""
            <tr>
                <td colspan="4" style="text-align: center; color: #64748b; padding: 0.75rem;">
                    Nenhum dividendo registrado no período. Proventos projetados: {fmt_brl(d['proj_div_mensal'])}/mês.
                </td>
            </tr>
            """

        # Linhas de oportunidades
        radar_rows_html = ""
        for item in d["radar_oportunidades"]:
            status_color = "#16a34a" if item["margem"] >= 0 else "#dc2626"
            radar_rows_html += f"""
            <tr>
                <td><strong>{item['ticker']}</strong> <span style="font-size:0.75rem; color:#64748b;">({item['empresa']})</span></td>
                <td>{item['setor']}</td>
                <td style="text-align: right;">{fmt_brl(item['preco_atual'])}</td>
                <td style="text-align: right; font-weight: 600;">{fmt_brl(item['preco_teto'])}</td>
                <td style="text-align: right; color: {status_color}; font-weight: 700;">{item['margem']:+.1f}%</td>
                <td style="text-align: right; font-weight: 600;">{item['dy_proj']:.1f}%</td>
                <td style="text-align: center;"><span style="background: rgba(34,197,94,0.15); color:#16a34a; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 700;">{item['status']}</span></td>
            </tr>
            """

        # Setores
        setores_html = ""
        for s in d["setores_dist"]:
            setores_html += f"""
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem; font-size: 0.85rem;">
                <span style="font-weight: 500;">{s['setor']}</span>
                <span style="font-weight: 700; color: #0284c7;">{s['pct']}% ({fmt_brl(s['valor'])})</span>
            </div>
            <div style="background: #e2e8f0; height: 6px; border-radius: 3px; overflow: hidden; margin-bottom: 0.6rem;">
                <div style="background: #0284c7; width: {s['pct']}%; height: 100%;"></div>
            </div>
            """

        html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>Fechamento Barsi - {d['mes_ano']}</title>
  <style>
    @page {{
      size: A4 portrait;
      margin: 10mm 12mm;
    }}
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      color: #0f172a;
      background: #ffffff;
      line-height: 1.45;
      font-size: 11pt;
    }}
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 2px solid #0284c7;
      padding-bottom: 0.75rem;
      margin-bottom: 1.25rem;
    }}
    .header h1 {{
      font-size: 18pt;
      color: #0f172a;
      font-weight: 800;
      letter-spacing: -0.5px;
    }}
    .header p {{
      color: #64748b;
      font-size: 8.5pt;
      margin-top: 2px;
    }}
    .badge-month {{
      background: #0284c7;
      color: #fff;
      padding: 0.35rem 0.8rem;
      border-radius: 6px;
      font-weight: 700;
      font-size: 11pt;
      text-transform: uppercase;
      text-align: right;
    }}
    .grid-kpi {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 0.75rem;
      margin-bottom: 1.25rem;
    }}
    .kpi-card {{
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 0.75rem;
    }}
    .kpi-card h4 {{
      font-size: 7.5pt;
      text-transform: uppercase;
      color: #64748b;
      margin-bottom: 0.25rem;
      font-weight: 700;
    }}
    .kpi-card .val {{
      font-size: 13pt;
      font-weight: 800;
      color: #0f172a;
    }}
    .section-title {{
      font-size: 11pt;
      font-weight: 700;
      color: #0f172a;
      border-left: 4px solid #0284c7;
      padding-left: 0.5rem;
      margin-bottom: 0.65rem;
      margin-top: 1rem;
    }}
    .grid-2col {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1rem;
      margin-bottom: 1rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 8.5pt;
      margin-bottom: 0.75rem;
    }}
    th, td {{
      padding: 0.45rem 0.5rem;
      border-bottom: 1px solid #e2e8f0;
      text-align: left;
    }}
    th {{
      background: #f1f5f9;
      color: #475569;
      font-weight: 700;
      text-transform: uppercase;
      font-size: 7.5pt;
    }}
    .box-budget {{
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 0.75rem;
    }}
    .budget-row {{
      display: flex;
      justify-content: space-between;
      padding: 0.25rem 0;
      font-size: 8.5pt;
      border-bottom: 1px dashed #e2e8f0;
    }}
    .budget-row:last-child {{
      border-bottom: none;
      font-weight: 700;
      padding-top: 0.4rem;
    }}
    .quote-box {{
      background: #f0f9ff;
      border: 1px solid #bae6fd;
      border-radius: 6px;
      padding: 0.65rem 0.85rem;
      margin-top: 1.25rem;
      font-size: 8pt;
      color: #0369a1;
      font-style: italic;
    }}
    .footer {{
      margin-top: 1.25rem;
      border-top: 1px solid #e2e8f0;
      padding-top: 0.5rem;
      display: flex;
      justify-content: space-between;
      font-size: 7.5pt;
      color: #94a3b8;
    }}
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>📈 Barsi Quant IA — Fechamento Mensal</h1>
      <p>Dossiê Executivo de Previdência Privada em Ações B.E.S.T. & Dividendos</p>
    </div>
    <div class="badge-month">
      <div>{d['mes_ano']}</div>
      <div style="font-size: 7pt; font-weight: normal; opacity: 0.85;">Emissão: {d['data_emissao']}</div>
    </div>
  </div>

  <!-- Indicadores Macro da Carteira -->
  <div class="grid-kpi">
    <div class="kpi-card">
      <h4>Patrimônio Total</h4>
      <div class="val" style="color: #0284c7;">{fmt_brl(d['patrimonio_total'])}</div>
      <div style="font-size: 7.5pt; color: #64748b; margin-top: 2px;">{d['total_cotas']:,} cotas totais</div>
    </div>
    <div class="kpi-card">
      <h4>Proventos no Mês</h4>
      <div class="val" style="color: #16a34a;">{fmt_brl(d['total_divs_mes'])}</div>
      <div style="font-size: 7.5pt; color: #64748b; margin-top: 2px;">Ano: {fmt_brl(d['total_divs_ano'])}</div>
    </div>
    <div class="kpi-card">
      <h4>Renda Mensal Projetada</h4>
      <div class="val" style="color: #9333ea;">{fmt_brl(d['proj_div_mensal'])}/mês</div>
      <div style="font-size: 7.5pt; color: #64748b; margin-top: 2px;">Anual: {fmt_brl(d['proj_div_anual'])}</div>
    </div>
    <div class="kpi-card">
      <h4>Yield on Cost Médio</h4>
      <div class="val" style="color: #ea580c;">{d['yoc_medio']:.1f}% a.a.</div>
      <div style="font-size: 7.5pt; color: #64748b; margin-top: 2px;">Retorno sobre custo real</div>
    </div>
  </div>

  <div class="grid-2col">
    <!-- Balanço Orçamentário e Capacidade de Aporte -->
    <div>
      <div class="section-title">💰 Balanço de Fluxo & Poupança ({d['mes_ano']})</div>
      <div class="box-budget">
        <div class="budget-row"><span>(+) Receitas / Entradas:</span> <strong style="color: #16a34a;">{fmt_brl(d['total_entradas'])}</strong></div>
        <div class="budget-row"><span>(-) Despesas Fixas (Moradia/Educação):</span> <span>{fmt_brl(d['desp_fixa'])}</span></div>
        <div class="budget-row"><span>(-) Despesas Variáveis (Consumo):</span> <span>{fmt_brl(d['desp_variavel'])}</span></div>
        <div class="budget-row"><span>(-) Tributos & Taxas:</span> <span>{fmt_brl(d['tributos'])}</span></div>
        <div class="budget-row" style="border-top: 1px solid #cbd5e1; margin-top: 4px;">
          <span>(=) Poupança Efetiva para Aporte:</span>
          <strong style="color: #0284c7; font-size: 9.5pt;">{fmt_brl(d['poupanca_gerada'])} ({d['taxa_poupanca_pct']}%)</strong>
        </div>
      </div>
    </div>

    <!-- Composição Setorial B.E.S.T. -->
    <div>
      <div class="section-title">🏛️ Equilíbrio Setorial B.E.S.T.</div>
      <div class="box-budget">
        {setores_html if setores_html else "<p style='color:#64748b; font-size:8.5pt;'>Nenhum ativo alocado no momento.</p>"}
      </div>
    </div>
  </div>

  <!-- Dividendos do Mês -->
  <div class="section-title">❄️ Proventos & Juros Compostos no Período</div>
  <table>
    <thead>
      <tr>
        <th>Ativo</th>
        <th>Data do Pagamento</th>
        <th>Tipo</th>
        <th style="text-align: right;">Valor Recebido</th>
      </tr>
    </thead>
    <tbody>
      {div_rows_html}
    </tbody>
  </table>

  <!-- Plano de Aportes para o Próximo Mês -->
  <div class="section-title">🎯 Plano de Aportes Prioritários (Radar B.E.S.T.)</div>
  <table>
    <thead>
      <tr>
        <th>Ativo / Empresa</th>
        <th>Setor</th>
        <th style="text-align: right;">Cotação</th>
        <th style="text-align: right;">Preço Teto</th>
        <th style="text-align: right;">Margem Segurança</th>
        <th style="text-align: right;">DY Projetado</th>
        <th style="text-align: center;">Recomendação</th>
      </tr>
    </thead>
    <tbody>
      {radar_rows_html if radar_rows_html else "<tr><td colspan='7' style='text-align:center;'>Nenhum ativo no radar.</td></tr>"}
    </tbody>
  </table>

  <div class="quote-box">
    <strong>💡 Filosofia Luiz Barsi Filho:</strong> "O dividendo é a única certeza matemática no mercado financeiro. Quem compra ações para receber dividendos tem paciência, disciplina e foco no longo prazo, transformando cada aporte mensal num tijolo da sua independência."
  </div>

  <div class="footer">
    <span>Barsi Quant IA v3.0 • Relatório Oficial emitido em SQLite Canônico</span>
    <span>Página 1 de 1</span>
  </div>
</body>
</html>
"""
        return html

    def generate_pdf_report(self, mes_ano: Optional[str] = None) -> bytes:
        """Compila o relatório HTML em PDF vetorial utilizando Playwright Headless Chromium.
        Executa em ThreadPool isolado para evitar conflito com loops asyncio ativos."""
        import concurrent.futures
        html_content = self.generate_html_report(mes_ano)

        def _render_worker(content: str) -> bytes:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_content(content, wait_until="load")
                pdf = page.pdf(
                    format="A4",
                    print_background=True,
                    margin={"top": "8mm", "bottom": "8mm", "left": "10mm", "right": "10mm"}
                )
                browser.close()
                return pdf

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                pdf_bytes = executor.submit(_render_worker, html_content).result(timeout=30)
            logger.info("pdf_report_generated", mes_ano=mes_ano, size_bytes=len(pdf_bytes))
            return pdf_bytes
        except Exception as e:
            logger.error("pdf_generation_failed", error=str(e))
            raise RuntimeError(f"Falha ao compilar PDF executivo: {e}")

    async def send_closing_via_telegram(self, notifier: Any, mes_ano: Optional[str] = None) -> bool:
        """Envia o Fechamento Mensal diretamente pelo Telegram.
        Tenta enviar o documento PDF; se não houver ambiente gráfico/Chromium na TV Box,
        envia o resumo executivo detalhado em formato de texto Markdown sem travar a TV Box."""
        data = self.get_closing_data(mes_ano)
        target = data["mes_ano"]
        patrimonio = data.get("patrimonio_total", 0.0)
        divs_mes = data.get("total_divs_mes", 0.0)
        yoc = data.get("yoc_medio", 0.0)
        poupanca = data.get("poupanca_gerada", 0.0)
        radar = data.get("radar_items", [])
        top_acao = radar[0]["ticker"] if radar else "Em formação"

        caption = (
            f"📊 *FECHAMENTO EXECUTIVO BARSI — {target}*\n\n"
            f"💼 *Patrimônio Total:* R$ {patrimonio:,.2f}\n"
            f"💰 *Proventos no Mês:* R$ {divs_mes:,.2f}\n"
            f"📈 *Yield on Cost (YoC):* {yoc:.1f}%\n"
            f"💵 *Capacidade de Aporte:* R$ {poupanca:,.2f}\n"
            f"🎯 *Top Aporte:* {top_acao}\n\n"
            f"🏛️ _Barsi Quant IA v3.0 • Autônomo na TV Box_"
        )

        # 1. Tenta compilar e enviar o PDF se houver suporte
        try:
            pdf_bytes = self.generate_pdf_report(target)
            if hasattr(notifier, "send_document"):
                sent = await notifier.send_document(
                    filename=f"Fechamento_Barsi_{target}.pdf",
                    content=pdf_bytes,
                    caption=caption
                )
                if sent:
                    return True
        except Exception as pdf_err:
            logger.info("pdf_fallback_to_text_closing", reason=str(pdf_err))

        # 2. Fallback resiliente (Headless / TV Box 0MB RAM extra) via texto rico
        if hasattr(notifier, "notify"):
            return await notifier.notify(
                title=f"Fechamento Mensal Barsi ({target})",
                body=caption,
                level="info"
            )
        elif hasattr(notifier, "send_message"):
            return await notifier.send_message(caption)
        return False
