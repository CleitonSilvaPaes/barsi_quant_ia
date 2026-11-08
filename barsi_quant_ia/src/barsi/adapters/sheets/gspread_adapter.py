"""Adapter para Google Sheets (gspread) com detecção inteligente de layout."""
import os
import asyncio
from typing import List, Optional
from datetime import datetime
import gspread

from barsi.ports.sheets import SheetsPort
from barsi.domain.models import Position, AcaoBarsi, SimuladorOrder
from barsi.infra.logging import logger

class GSpreadAdapter(SheetsPort):
    def __init__(self, credentials_path: str, spreadsheet_id: str = "", sqlite_db_path: str = "./memory/barsi_memory.db"):
        self.credentials_path = credentials_path
        self.spreadsheet_id = spreadsheet_id
        self.sqlite_db_path = sqlite_db_path
        from barsi.adapters.history.budget_repository import BudgetRepository
        self.budget_repo = BudgetRepository(sqlite_db_path)

    def _get_client_and_spreadsheet(self):
        gc = gspread.service_account(filename=self.credentials_path)
        if self.spreadsheet_id:
            return gc.open_by_key(self.spreadsheet_id)
        
        # Tenta abrir pelo título novo ou pelo legado
        for title in ["Carteira Barsi Quant IA B.E.S.T.", "Carteira Barsi Quant IA"]:
            try:
                return gc.open(title)
            except gspread.SpreadsheetNotFound:
                continue
        raise gspread.SpreadsheetNotFound("Nenhuma planilha Barsi encontrada no Google Drive.")

    def _parse_currency(self, val) -> float:
        if not val or "#" in str(val):
            return 0.0
        try:
            clean = str(val).replace("R$", "").replace("%", "").replace(".", "").replace(",", ".").strip()
            return float(clean)
        except Exception:
            return 0.0

    async def load_positions(self) -> List[Position]:
        def _load():
            sh = self._get_client_and_spreadsheet()
            worksheet_titles = [w.title for w in sh.worksheets()]

            # Cenário 1: Planilha Pro B.E.S.T. ('Screener_BEST_Custodia')
            if "Screener_BEST_Custodia" in worksheet_titles:
                ws = sh.worksheet("Screener_BEST_Custodia")
                rows = ws.get("A10:P19")

                # Mapeia feedbacks do usuário gravados na aba de IA
                feedbacks_map = {}
                if "IA_2B_Insights_Retroalimentacao" in worksheet_titles:
                    try:
                        ws_ia = sh.worksheet("IA_2B_Insights_Retroalimentacao")
                        ia_rows = ws_ia.get("A11:M20")
                        for ir in ia_rows:
                            if len(ir) > 11 and ir[2]:
                                feedbacks_map[str(ir[2]).strip().upper()] = str(ir[11]).strip()
                    except Exception:
                        pass

                positions = []
                for idx, r in enumerate(rows, start=10):
                    if not r or not r[0]:
                        continue
                    ticker = str(r[0]).strip().upper()
                    setor = str(r[1]).strip() if len(r) > 1 else ""
                    empresa = str(r[2]).strip() if len(r) > 2 else ""

                    qtd_antes = 0
                    if len(r) > 3 and r[3]:
                        try:
                            qtd_antes = int(float(str(r[3]).replace(".", "").replace(",", ".").strip()))
                        except Exception:
                            qtd_antes = 0

                    qtd_aporte = 0
                    if len(r) > 4 and r[4]:
                        try:
                            qtd_aporte = int(float(str(r[4]).replace(".", "").replace(",", ".").strip()))
                        except Exception:
                            qtd_aporte = 0

                    qtd_total = 0
                    if len(r) > 5 and r[5]:
                        try:
                            qtd_total = int(float(str(r[5]).replace(".", "").replace(",", ".").strip()))
                        except Exception:
                            qtd_total = qtd_antes + qtd_aporte
                    else:
                        qtd_total = qtd_antes + qtd_aporte

                    # Col H (8) = Cotação Atual, Col G (7) = Cotação Base
                    preco = self._parse_currency(r[7] if len(r) > 7 else None)
                    if preco == 0.0 and len(r) > 6:
                        preco = self._parse_currency(r[6])
                    
                    # Col I (9) = DPA Projetado
                    div = self._parse_currency(r[8] if len(r) > 8 else None)
                    teto = self._parse_currency(r[9] if len(r) > 9 else None)
                    margem = self._parse_currency(r[10] if len(r) > 10 else None)
                    dy = self._parse_currency(r[11] if len(r) > 11 else None)
                    valor_total = self._parse_currency(r[12] if len(r) > 12 else None)
                    total_aporte = self._parse_currency(r[14] if len(r) > 14 else None)

                    fb = feedbacks_map.get(ticker, "")

                    positions.append(Position(
                        ticker=ticker,
                        row_index=idx,
                        empresa=empresa,
                        setor=setor,
                        qtd_antes=qtd_antes,
                        qtd_aporte=qtd_aporte,
                        qtd_total=qtd_total,
                        preco_atual=preco,
                        div_proj=div,
                        preco_teto=teto,
                        margem_seguranca=margem,
                        dy=dy,
                        valor_total=valor_total,
                        total_aporte=total_aporte,
                        feedback_usuario=fb
                    ))
                return positions

            # Cenário 2: Layout Legado ('Dashboard Barsi')
            ws = sh.worksheet("Dashboard Barsi")
            rows = ws.get("A7:M16")
            positions = []
            for idx, r in enumerate(rows, start=7):
                if not r or not r[0]:
                    continue
                ticker = str(r[0]).strip().upper()
                preco = self._parse_currency(r[3] if len(r) > 3 else None)
                div = self._parse_currency(r[5] if len(r) > 5 else None)
                feedback = str(r[12]).strip() if len(r) > 12 and r[12] else ""
                positions.append(Position(
                    ticker=ticker,
                    row_index=idx,
                    preco_atual=preco,
                    div_proj=div,
                    feedback_usuario=feedback
                ))
            return positions

        return await asyncio.to_thread(_load)

    async def load_simulador_orders(self) -> List[SimuladorOrder]:
        """Carrega as ordens programadas do Simulador de Aportes B.E.S.T."""
        def _load_sim():
            sh = self._get_client_and_spreadsheet()
            worksheet_titles = [w.title for w in sh.worksheets()]
            if "Simulador_Aportes_Barsi" not in worksheet_titles:
                return []
            ws = sh.worksheet("Simulador_Aportes_Barsi")
            rows = ws.get("A12:O16")
            orders = []
            for r in rows:
                if not r or not r[0] or not str(r[0]).strip().isdigit():
                    continue
                try:
                    rank = int(r[0])
                    ticker = str(r[1]).strip().upper()
                    empresa = str(r[2]).strip()
                    setor = str(r[3]).strip()
                    cotacao = self._parse_currency(r[4])
                    teto = self._parse_currency(r[5])
                    margem = self._parse_currency(r[6])
                    aloc = self._parse_currency(r[7])
                    orc = self._parse_currency(r[8])
                    qtd_frac = int(float(str(r[9]).replace(",", ".")))
                    gasto = self._parse_currency(r[10])
                    troco = self._parse_currency(r[11])
                    prov = self._parse_currency(r[12])
                    yd = self._parse_currency(r[13])
                    status = str(r[14]).strip() if len(r) > 14 else ""
                    orders.append(SimuladorOrder(
                        rank=rank,
                        ticker=ticker,
                        empresa=empresa,
                        setor=setor,
                        cotacao=cotacao,
                        preco_teto=teto,
                        margem_seg=margem,
                        alocacao_meta_pct=aloc,
                        orcamento_teorico=orc,
                        qtd_fracionaria=qtd_frac,
                        gasto_efetivo=gasto,
                        troco=troco,
                        provento_anual_adic=prov,
                        yield_compra_pct=yd,
                        status_ordem=status
                    ))
                except Exception as e:
                    logger.warn("simulador_parse_error", row=r, error=str(e))
            return orders

        return await asyncio.to_thread(_load_sim)

    async def update_positions(self, positions: List[Position]) -> None:
        def _update():
            sh = self._get_client_and_spreadsheet()
            worksheet_titles = [w.title for w in sh.worksheets()]

            # Cenário 1: Planilha Pro B.E.S.T.
            if "Screener_BEST_Custodia" in worksheet_titles:
                ws = sh.worksheet("Screener_BEST_Custodia")
                updates = []
                for p in positions:
                    row = p.row_index
                    # Col D: Custódia Atual (Qtd Antes)
                    updates.append({"range": f"D{row}", "values": [[p.qtd_antes]]})
                    # Col E: Aporte Programado (Qtd)
                    updates.append({"range": f"E{row}", "values": [[p.qtd_aporte]]})
                    # Col F: Qtd Total Atual
                    qtd_tot = p.qtd_antes + p.qtd_aporte
                    updates.append({"range": f"F{row}", "values": [[qtd_tot]]})
                    # Col H: Cotação Atual (numérico puro para viabilizar fórmulas)
                    updates.append({"range": f"H{row}", "values": [[round(p.preco_atual, 2)]]})
                    # Col I: DPA Projetado
                    updates.append({"range": f"I{row}", "values": [[round(p.div_proj, 2)]]})
                    # Col J: Preço Teto Barsi (numérico puro)
                    updates.append({"range": f"J{row}", "values": [[round(p.preco_teto, 2)]]})
                    # Col K: Margem de Segurança (decimal puro para exibição em %)
                    updates.append({"range": f"K{row}", "values": [[round(p.margem_seguranca / 100.0, 4)]]})
                    # Col M: Valor Total Posição (R$)
                    val_tot = round(qtd_tot * p.preco_atual, 2)
                    updates.append({"range": f"M{row}", "values": [[f"R$ {val_tot:.2f}".replace(".", ",")]]})
                    # Col O: Total Aporte Nov (R$)
                    tot_ap = round(p.qtd_aporte * p.preco_atual, 2)
                    updates.append({"range": f"O{row}", "values": [[f"R$ {tot_ap:.2f}".replace(".", ",")]]})
                    # Col P: Status Recomendação Barsi
                    updates.append({"range": f"P{row}", "values": [[p.status_barsi.value]]})
                ws.batch_update(updates)

                # Se houver a aba de IA, registra o log e os feedbacks
                if "IA_2B_Insights_Retroalimentacao" in worksheet_titles:
                    ws_ia = sh.worksheet("IA_2B_Insights_Retroalimentacao")
                    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
                    log_rows = []
                    for idx, p in enumerate(positions, start=1):
                        log_rows.append([
                            f"LOG-{idx:03d}",
                            now_str,
                            p.ticker,
                            f"R$ {p.preco_atual:.2f}".replace(".", ","),
                            f"R$ {p.preco_teto:.2f}".replace(".", ","),
                            f"{p.margem_seguranca:.2f}%".replace(".", ","),
                            "Notícias B3/Google News atualizadas",
                            p.insight_atual or f"Score B.E.S.T.: Margem {p.margem_seguranca:.1f}%. Status: {p.status_barsi.value}",
                            p.status_barsi.value,
                            str(p.qtd_aporte) if p.qtd_aporte > 0 else "N/A",
                            "Concluído",
                            p.feedback_usuario,
                            "B.E.S.T. Quant"
                        ])
                    ws_ia.update(range_name="A11:M20", values=log_rows)

                logger.info("google_sheets_pro_updated", count=len(positions))
                self._sync_to_local_xlsx(positions)
                return

            # Cenário 2: Layout Legado
            ws = sh.worksheet("Dashboard Barsi")
            updates = []
            for p in positions:
                row = p.row_index
                updates.append({"range": f"D{row}", "values": [[p.preco_atual]]})
                updates.append({"range": f"I{row}", "values": [[p.status_barsi.value]]})
                updates.append({"range": f"L{row}", "values": [[p.insight_atual]]})
            ws.batch_update(updates)
            logger.info("google_sheets_batch_updated", count=len(positions))

        await asyncio.to_thread(_update)

    def _col_idx_to_letter(self, idx: int) -> str:
        """Converte índice numérico (1, 2, 3...) em letra de coluna Excel/Sheets (A, B, C...)."""
        string = ""
        while idx > 0:
            idx, remainder = divmod(idx - 1, 26)
            string = chr(65 + remainder) + string
        return string

    def sync_budget_manager(self, resumos: dict):
        """Sincroniza os valores de conciliação de extratos com a aba Budget_Manager_Fluxo de forma 100% dinâmica e cronológica."""
        sh = self._get_client_and_spreadsheet()
        worksheet_titles = [w.title for w in sh.worksheets()]
        if "Budget_Manager_Fluxo" not in worksheet_titles:
            logger.warning("budget_manager_sheet_not_found")
            return
            
        ws = sh.worksheet("Budget_Manager_Fluxo")
        all_rows = ws.get_all_values()
        if len(all_rows) < 10:
            return

        meses_pt = {
            "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
            "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
            "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro"
        }

        # Filtra e ordena estritamente por ordem cronológica (YYYY-MM)
        meses_ordenados = sorted([
            m for m, r in resumos.items() 
            if r.total_entradas > 0 or r.total_saidas > 0 or r.salario_entradas > 0 or r.compras_acoes_barsi > 0
        ])
        if not meses_ordenados:
            return

        # Monta os cabeçalhos cronológicos estritos
        headers = ["Item / Descrição Orçamentária"]
        for m in meses_ordenados:
            ano, mes = m.split("-")
            headers.append(f"{meses_pt.get(mes, mes)}/{ano} (R$)")

        ultimo_mes_chave = meses_ordenados[-1]
        ano_ult, mes_ult = ultimo_mes_chave.split("-")
        mes_ult_nome = meses_pt.get(mes_ult, mes_ult)
        mes_ult_abrev = mes_ult_nome[:3].upper()
        ano_ult_abrev = ano_ult[2:]

        headers.extend([
            "Variação Nominal (R$)",
            f"% Orçamento {mes_ult_nome[:3]}",
            "Status de Execução",
            "Meio de Liquidação"
        ])

        total_cols = len(headers)
        if ws.col_count < total_cols:
            ws.add_cols(total_cols - ws.col_count)
        elif ws.col_count > total_cols:
            ws.delete_columns(total_cols + 1, ws.col_count)

        # 1. Configura merges simétricos dos 4 Cards Superiores (Linhas 4, 5, 6)
        body_merges = {
            'requests': [
                {'unmergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 3, 'endRowIndex': 6, 'startColumnIndex': 0, 'endColumnIndex': 16}}},
                # Card 1: A:D (cols 0..4)
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 3, 'endRowIndex': 4, 'startColumnIndex': 0, 'endColumnIndex': 4}, 'mergeType': 'MERGE_ALL'}},
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 4, 'endRowIndex': 5, 'startColumnIndex': 0, 'endColumnIndex': 4}, 'mergeType': 'MERGE_ALL'}},
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 5, 'endRowIndex': 6, 'startColumnIndex': 0, 'endColumnIndex': 4}, 'mergeType': 'MERGE_ALL'}},
                # Card 2: E:H (cols 4..8)
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 3, 'endRowIndex': 4, 'startColumnIndex': 4, 'endColumnIndex': 8}, 'mergeType': 'MERGE_ALL'}},
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 4, 'endRowIndex': 5, 'startColumnIndex': 4, 'endColumnIndex': 8}, 'mergeType': 'MERGE_ALL'}},
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 5, 'endRowIndex': 6, 'startColumnIndex': 4, 'endColumnIndex': 8}, 'mergeType': 'MERGE_ALL'}},
                # Card 3: I:L (cols 8..12)
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 3, 'endRowIndex': 4, 'startColumnIndex': 8, 'endColumnIndex': 12}, 'mergeType': 'MERGE_ALL'}},
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 4, 'endRowIndex': 5, 'startColumnIndex': 8, 'endColumnIndex': 12}, 'mergeType': 'MERGE_ALL'}},
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 5, 'endRowIndex': 6, 'startColumnIndex': 8, 'endColumnIndex': 12}, 'mergeType': 'MERGE_ALL'}},
                # Card 4: M:P (cols 12..16)
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 3, 'endRowIndex': 4, 'startColumnIndex': 12, 'endColumnIndex': 16}, 'mergeType': 'MERGE_ALL'}},
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 4, 'endRowIndex': 5, 'startColumnIndex': 12, 'endColumnIndex': 16}, 'mergeType': 'MERGE_ALL'}},
                {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 5, 'endRowIndex': 6, 'startColumnIndex': 12, 'endColumnIndex': 16}, 'mergeType': 'MERGE_ALL'}},
            ]
        }
        try:
            sh.batch_update(body_merges)
        except Exception as e:
            logger.warning("batch_update_merges_warning", error=str(e))

        # 2. Atualiza cabeçalho linha 9
        range_h = f"A9:{self._col_idx_to_letter(total_cols)}9"
        ws.update(range_name=range_h, values=[headers], value_input_option="USER_ENTERED")

        updates = []

        # Preenche colunas de cada mês cronológico (Linhas 11 a 45)
        for col_idx, m in enumerate(meses_ordenados, start=2):
            c_let = self._col_idx_to_letter(col_idx)
            r = resumos[m]

            # Resolve todas as despesas vigentes para o mês de referência 'm' via SQLite (Zero Magic Numbers)
            expenses = self.budget_repo.get_all_expenses(m)
            def _expense(name: str) -> float:
                val = expenses.get(name, 0.0)
                if val == 0.0:
                    logger.debug("expense_missing_in_db", mes=m, category=name)
                return val

            salario = round(r.salario_entradas, 2)
            proventos = round(r.proventos_recebidos, 2)
            saldo_ant = _expense("Saldo Inicial Nov/26") or _expense("Saldo Inicial")

            updates.append({"range": f"{c_let}11", "values": [[salario]]})
            updates.append({"range": f"{c_let}12", "values": [[proventos]]})
            updates.append({"range": f"{c_let}13", "values": [[saldo_ant]]})
            updates.append({"range": f"{c_let}14", "values": [[f"=SUM({c_let}11:{c_let}13)"]]})

            fatura = round(r.faturas_cartao, 2)
            aluguel = _expense("Aluguel")
            comp_familiar = _expense("Compromisso Familiar")
            das = _expense("DAS Simples Nacional")
            licenciamento = _expense("Licenciamento Detran")
            ailos = _expense("Cooperativa Ailos")

            updates.append({"range": f"{c_let}17", "values": [[fatura]]})
            updates.append({"range": f"{c_let}18", "values": [[aluguel]]})
            updates.append({"range": f"{c_let}19", "values": [[comp_familiar]]})
            updates.append({"range": f"{c_let}20", "values": [[das]]})
            updates.append({"range": f"{c_let}21", "values": [[licenciamento]]})
            updates.append({"range": f"{c_let}22", "values": [[ailos]]})
            updates.append({"range": f"{c_let}23", "values": [[f"=SUM({c_let}17:{c_let}22)"]]})

            saldo_ant_caixa = _expense("Saldo Ant. Caixa")
            novo_dep = round(r.reserva_rdb, 2)
            updates.append({"range": f"{c_let}26", "values": [[saldo_ant_caixa]]})
            updates.append({"range": f"{c_let}27", "values": [[novo_dep]]})
            updates.append({"range": f"{c_let}28", "values": [[f"={c_let}26+{c_let}27"]]})

            acoes = round(r.compras_acoes_barsi, 2)
            fiis = round(r.compras_fiis, 2)
            updates.append({"range": f"{c_let}31", "values": [[acoes]]})
            updates.append({"range": f"{c_let}32", "values": [[fiis]]})
            updates.append({"range": f"{c_let}33", "values": [[f"=SUM({c_let}31:{c_let}32)"]]})

            # Gastos Variáveis (Linhas 36..40)
            v36 = _expense("Gastos Variáveis A")
            v37 = _expense("Gastos Variáveis B")
            v38 = _expense("Gastos Variáveis C")
            v39 = _expense("Gastos Variáveis D")
            v40 = _expense("Gastos Variáveis E")

            updates.append({"range": f"{c_let}36", "values": [[v36]]})
            updates.append({"range": f"{c_let}37", "values": [[v37]]})
            updates.append({"range": f"{c_let}38", "values": [[v38]]})
            updates.append({"range": f"{c_let}39", "values": [[v39]]})
            updates.append({"range": f"{c_let}40", "values": [[v40]]})

            updates.append({"range": f"{c_let}41", "values": [[f"=SUM({c_let}36:{c_let}40)"]]})
            updates.append({"range": f"{c_let}44", "values": [[f"={c_let}23+{c_let}27+{c_let}33+{c_let}41"]]})
            updates.append({"range": f"{c_let}45", "values": [[f"={c_let}14-{c_let}44"]]})

        # Colunas de Variação Nominal e % Orçamento
        penultimo_col = self._col_idx_to_letter(1 + len(meses_ordenados) - 1)
        ultimo_col = self._col_idx_to_letter(1 + len(meses_ordenados))
        var_col = self._col_idx_to_letter(1 + len(meses_ordenados) + 1)
        pct_col = self._col_idx_to_letter(1 + len(meses_ordenados) + 2)

        for r in range(11, 46):
            if r in [15, 16, 24, 25, 29, 30, 34, 35, 42, 43]:
                continue
            updates.append({"range": f"{var_col}{r}", "values": [[f"={ultimo_col}{r}-{penultimo_col}{r}"]]})
            updates.append({"range": f"{pct_col}{r}", "values": [[f"={ultimo_col}{r}/{ultimo_col}$14"]]})

        # 3. Atualiza os 4 Cards de Topo
        updates.append({"range": "A4", "values": [[f"ENTRADAS TOTAIS ({mes_ult_abrev}/{ano_ult_abrev})"]]})
        updates.append({"range": "A5", "values": [[f"={ultimo_col}14"]]})
        updates.append({"range": "A6", "values": [["Salário + Proventos + Saldo anterior"]]})

        updates.append({"range": "E4", "values": [["SAÍDAS FIXAS OBRIGATÓRIAS"]]})
        updates.append({"range": "E5", "values": [[f"={ultimo_col}23"]]})
        updates.append({"range": "E6", "values": [[f"=TEXT({ultimo_col}23/{ultimo_col}14; \"0,0%\") & \" da renda comprometida\""]]})

        updates.append({"range": "I4", "values": [["APORTES + RESERVA"]]})
        updates.append({"range": "I5", "values": [[f"={ultimo_col}27+{ultimo_col}33"]]})
        updates.append({"range": "I6", "values": [[f"=TEXT(({ultimo_col}27+{ultimo_col}33)/{ultimo_col}14; \"0,0%\") & \" de taxa de poupança\""]]})

        updates.append({"range": "M4", "values": [["SALDO LIVRE RESIDUAL"]]})
        updates.append({"range": "M5", "values": [[f"={ultimo_col}45"]]})
        updates.append({"range": "M6", "values": [["Folga livre pós-contas e aportes"]]})

        # Título Linha 8
        mes_ini_nome = meses_pt.get(meses_ordenados[0].split("-")[1], "").upper()
        mes_fim_nome = mes_ult_nome.upper()
        updates.append({"range": "A8", "values": [[f"CONCILIAÇÃO ORÇAMENTÁRIA DETALHADA ({mes_ini_nome} A {mes_fim_nome} {ano_ult})"]]})

        ws.batch_update(updates, value_input_option="USER_ENTERED")
        logger.info("budget_manager_dynamic_synced", months=len(meses_ordenados), updates=len(updates))

    def _sync_to_local_xlsx(self, positions: List[Position], file_path: str = "carteira_barsi_quant_ia.xlsx"):
        """Atualiza a planilha Excel local (.xlsx) mantendo paridade 100% com o Google Sheets."""
        candidate_paths = [
            file_path,
            os.path.join("barsi_quant_ia", file_path),
            os.path.join(os.path.dirname(__file__), "../../../carteira_barsi_quant_ia.xlsx"),
            os.path.join(os.path.dirname(__file__), "../../../barsi_quant_ia/carteira_barsi_quant_ia.xlsx"),
        ]
        target_path = None
        for cp in candidate_paths:
            if os.path.exists(cp):
                target_path = cp
                break
        
        if not target_path:
            return

        try:
            import openpyxl
            wb = openpyxl.load_workbook(target_path)
            sheet_names = wb.sheetnames

            # Cenário 1: Layout Pro B.E.S.T.
            if "Screener_BEST_Custodia" in sheet_names:
                ws = wb["Screener_BEST_Custodia"]
                for p in positions:
                    r = p.row_index
                    qtd_tot = p.qtd_antes + p.qtd_aporte
                    ws[f"E{r}"] = p.qtd_aporte
                    ws[f"F{r}"] = qtd_tot
                    ws[f"H{r}"] = round(p.preco_atual, 2)
                    ws[f"I{r}"] = round(p.div_proj, 2)
                    ws[f"J{r}"] = round(p.preco_teto, 2)
                    ws[f"K{r}"] = round(p.margem_seguranca / 100.0, 4)
                    ws[f"M{r}"] = round(qtd_tot * p.preco_atual, 2)
                    ws[f"O{r}"] = round(p.qtd_aporte * p.preco_atual, 2)
                    ws[f"P{r}"] = p.status_barsi.value

                if "IA_2B_Insights_Retroalimentacao" in sheet_names:
                    ws_ia = wb["IA_2B_Insights_Retroalimentacao"]
                    for idx, p in enumerate(positions, start=11):
                        ws_ia[f"H{idx}"] = p.insight_atual or f"Score B.E.S.T. Margem {p.margem_seguranca:.1f}%"
                        ws_ia[f"I{idx}"] = p.status_barsi.value
                        ws_ia[f"J{idx}"] = p.qtd_aporte if p.qtd_aporte > 0 else "N/A"
                        ws_ia[f"L{idx}"] = p.feedback_usuario

            elif "Dashboard Barsi" in sheet_names:
                ws = wb["Dashboard Barsi"]
                for p in positions:
                    r = p.row_index
                    ws[f"D{r}"] = round(p.preco_atual, 2)
                    ws[f"F{r}"] = round(p.div_proj, 2)
                    ws[f"I{r}"] = p.status_barsi.value
                    ws[f"L{r}"] = p.insight_atual
            
            wb.save(target_path)
            wb.close()
            logger.info("local_xlsx_mirrored_successfully", path=target_path)
        except Exception as exc:
            logger.warn("local_xlsx_mirror_failed", error=str(exc))

