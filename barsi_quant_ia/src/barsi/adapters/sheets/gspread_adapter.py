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

    def sync_budget_manager(self, resumos: dict):
        """Sincroniza os valores de conciliação de extratos com a aba Budget_Manager_Fluxo."""
        # Pega o mês mais recente disponível nos resumos
        meses_ordenados = sorted(resumos.keys())
        if not meses_ordenados:
            return
        
        # Pega preferencialmente novembro/2026 se presente
        mes_alvo = "2026-11" if "2026-11" in resumos else meses_ordenados[-1]
        r = resumos[mes_alvo]
        
        sh = self._get_client_and_spreadsheet()
        worksheet_titles = [w.title for w in sh.worksheets()]
        if "Budget_Manager_Fluxo" not in worksheet_titles:
            logger.warning("budget_manager_sheet_not_found")
            return
            
        ws = sh.worksheet("Budget_Manager_Fluxo")
        updates = []
        
        if r.salario_entradas > 0:
            updates.append({"range": "C11", "values": [[f"R$ {r.salario_entradas:.2f}".replace(".", ",")]]})
        if r.proventos_recebidos > 0:
            updates.append({"range": "C12", "values": [[f"R$ {r.proventos_recebidos:.2f}".replace(".", ",")]]})
        if r.faturas_cartao > 0:
            updates.append({"range": "C17", "values": [[f"R$ {r.faturas_cartao:.2f}".replace(".", ",")]]})
        if r.reserva_rdb > 0:
            updates.append({"range": "C27", "values": [[f"R$ {r.reserva_rdb:.2f}".replace(".", ",")]]})
        if r.compras_acoes_barsi > 0:
            updates.append({"range": "C31", "values": [[f"R$ {r.compras_acoes_barsi:.2f}".replace(".", ",")]]})
        if r.compras_fiis > 0:
            updates.append({"range": "C32", "values": [[f"R$ {r.compras_fiis:.2f}".replace(".", ",")]]})
            
        if updates:
            ws.batch_update(updates)
            logger.info("budget_manager_synced", mes=mes_alvo, updates=len(updates))

