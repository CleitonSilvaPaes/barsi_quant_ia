"""Adapter para Google Sheets (gspread) com compatibilidade de layout."""
import os
import asyncio
from typing import List
from barsi.ports.sheets import SheetsPort
from barsi.domain.models import Position, AcaoBarsi
from barsi.infra.logging import logger
import gspread

"""Adapter para Google Sheets (gspread) com detecção inteligente de layout."""
import os
import asyncio
from typing import List, Optional
from datetime import datetime
from barsi.ports.sheets import SheetsPort
from barsi.domain.models import Position, AcaoBarsi
from barsi.infra.logging import logger
import gspread

class GSpreadAdapter(SheetsPort):
    def __init__(self, credentials_path: str, spreadsheet_id: str = ""):
        self.credentials_path = credentials_path
        self.spreadsheet_id = spreadsheet_id

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
                positions = []
                for idx, r in enumerate(rows, start=10):
                    if not r or not r[0]:
                        continue
                    ticker = str(r[0]).strip().upper()
                    # Col H (8) = Cotação Atual, Col G (7) = Cotação Base
                    preco = self._parse_currency(r[7] if len(r) > 7 else None)
                    if preco == 0.0 and len(r) > 6:
                        preco = self._parse_currency(r[6])
                    
                    # Col I (9) = DPA Projetado
                    div = self._parse_currency(r[8] if len(r) > 8 else None)
                    
                    positions.append(Position(
                        ticker=ticker,
                        row_index=idx,
                        preco_atual=preco,
                        div_proj=div,
                        feedback_usuario=""
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
                    # Col H: Cotação Atual (numérico puro para viabilizar fórmulas)
                    updates.append({"range": f"H{row}", "values": [[round(p.preco_atual, 2)]]})
                    # Col J: Preço Teto Barsi (numérico puro)
                    updates.append({"range": f"J{row}", "values": [[round(p.preco_teto, 2)]]})
                    # Col K: Margem de Segurança (decimal puro para exibição em %)
                    updates.append({"range": f"K{row}", "values": [[round(p.margem_seguranca / 100.0, 4)]]})
                    # Col P: Status Recomendação Barsi
                    updates.append({"range": f"P{row}", "values": [[p.status_barsi.value]]})
                ws.batch_update(updates)

                # Se houver a aba de IA, registra o log
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
                            p.insight_atual,
                            p.status_barsi.value,
                            "N/A",
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
        """Sincroniza os valores de conciliação de extratos com a aba Budget_Manager_Fluxo de forma 100% dinâmica."""
        sh = self._get_client_and_spreadsheet()
        worksheet_titles = [w.title for w in sh.worksheets()]
        if "Budget_Manager_Fluxo" not in worksheet_titles:
            logger.warning("budget_manager_sheet_not_found")
            return
            
        ws = sh.worksheet("Budget_Manager_Fluxo")
        all_rows = ws.get_all_values()
        if len(all_rows) < 10:
            return

        header_row_idx = 9  # Linha do cabeçalho de colunas orçamentárias
        header_vals = all_rows[header_row_idx - 1]

        # Mapeia linhas por palavra-chave na coluna A para nunca depender de números de linha fixos
        row_map = {}
        for r_idx, row in enumerate(all_rows, start=1):
            if not row:
                continue
            txt = row[0].strip().upper()
            if "SALÁRIO" in txt:
                row_map["salario"] = r_idx
            elif "PROVENTOS RECEBIDOS" in txt or "DIVIDENDOS" in txt:
                row_map["proventos"] = r_idx
            elif "FATURA CARTÃO" in txt or "FATURA" in txt:
                row_map["fatura"] = r_idx
            elif "NOVO DEPÓSITO NO COLCHÃO" in txt or "COLCHÃO" in txt:
                row_map["colchao"] = r_idx
            elif "AÇÕES B.E.S.T." in txt:
                row_map["acoes"] = r_idx
            elif "FII PAPEL" in txt or "MXRF11" in txt:
                row_map["fiis"] = r_idx

        meses_pt = {
            "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
            "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
            "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro"
        }

        updates = []
        for mes_chave, r in sorted(resumos.items()):
            if r.total_entradas == 0 and r.total_saidas == 0:
                continue

            partes = mes_chave.split("-")
            if len(partes) != 2:
                continue
            ano, mes_num = partes[0], partes[1]
            nome_mes = f"{meses_pt.get(mes_num, mes_num)}/{ano}"

            # Procura a coluna do mês no cabeçalho
            target_col_idx = None
            for c_idx, col_name in enumerate(header_vals, start=1):
                if nome_mes.lower() in col_name.lower():
                    target_col_idx = c_idx
                    break

            # Se o mês não existe na planilha (ex: Dezembro/2026 novo), insere a coluna automaticamente
            if not target_col_idx:
                insert_before_idx = len(header_vals)
                for c_idx, col_name in enumerate(header_vals, start=1):
                    if "VARIAÇÃO" in col_name.upper():
                        insert_before_idx = c_idx
                        break
                ws.insert_cols([[f"{nome_mes} (R$)"]], col=insert_before_idx)
                target_col_idx = insert_before_idx
                header_vals = ws.row_values(header_row_idx)

            col_letter = self._col_idx_to_letter(target_col_idx)

            if "salario" in row_map and r.salario_entradas > 0:
                updates.append({"range": f"{col_letter}{row_map['salario']}", "values": [[f"R$ {r.salario_entradas:.2f}".replace(".", ",")]]})
            if "proventos" in row_map and r.proventos_recebidos > 0:
                updates.append({"range": f"{col_letter}{row_map['proventos']}", "values": [[f"R$ {r.proventos_recebidos:.2f}".replace(".", ",")]]})
            if "fatura" in row_map and r.faturas_cartao > 0:
                updates.append({"range": f"{col_letter}{row_map['fatura']}", "values": [[f"R$ {r.faturas_cartao:.2f}".replace(".", ",")]]})
            if "colchao" in row_map and r.reserva_rdb > 0:
                updates.append({"range": f"{col_letter}{row_map['colchao']}", "values": [[f"R$ {r.reserva_rdb:.2f}".replace(".", ",")]]})
            if "acoes" in row_map and r.compras_acoes_barsi > 0:
                updates.append({"range": f"{col_letter}{row_map['acoes']}", "values": [[f"R$ {r.compras_acoes_barsi:.2f}".replace(".", ",")]]})
            if "fiis" in row_map and r.compras_fiis > 0:
                updates.append({"range": f"{col_letter}{row_map['fiis']}", "values": [[f"R$ {r.compras_fiis:.2f}".replace(".", ",")]]})

        if updates:
            ws.batch_update(updates)
            logger.info("budget_manager_dynamic_synced", updates=len(updates))

    def _sync_to_local_xlsx(self, positions: List[Position], file_path: str = "carteira_barsi_quant_ia.xlsx"):
        """Atualiza a planilha Excel local (.xlsx) mantendo paridade 100% com o Google Sheets."""
        if not os.path.exists(file_path):
            return
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path)
            sheet_names = wb.sheetnames

            # Cenário 1: Layout Pro
            if "Screener_BEST_Custodia" in sheet_names:
                ws = wb["Screener_BEST_Custodia"]
                for p in positions:
                    r = p.row_index
                    ws[f"H{r}"] = round(p.preco_atual, 2)
                    ws[f"J{r}"] = round(p.preco_teto, 2)
                    ws[f"K{r}"] = round(p.margem_seguranca / 100.0, 4)
                    ws[f"P{r}"] = p.status_barsi.value
            elif "Dashboard Barsi" in sheet_names:
                ws = wb["Dashboard Barsi"]
                for p in positions:
                    r = p.row_index
                    ws[f"D{r}"] = round(p.preco_atual, 2)
                    ws[f"I{r}"] = p.status_barsi.value
                    ws[f"L{r}"] = p.insight_atual
            
            wb.save(file_path)
            logger.info("local_xlsx_mirrored_successfully", path=file_path)
        except Exception as exc:
            logger.warn("local_xlsx_mirror_failed", error=str(exc))

