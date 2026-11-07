"""Adapter para Excel Local (.xlsx) usando openpyxl."""
import os
import asyncio
from typing import List
from barsi.ports.sheets import SheetsPort
from barsi.domain.models import Position, AcaoBarsi
from barsi.infra.logging import logger
import openpyxl
from openpyxl.styles import Font, Alignment

class XLSXAdapter(SheetsPort):
    def __init__(self, file_path: str = "carteira_barsi_quant_ia.xlsx"):
        self.file_path = file_path

    async def load_positions(self) -> List[Position]:
        def _load():
            if not os.path.exists(self.file_path):
                logger.error("xlsx_not_found", path=self.file_path)
                return []
            wb = openpyxl.load_workbook(self.file_path, data_only=True)
            ws = wb["Dashboard Barsi"]
            positions = []
            for row in range(7, 17):
                ticker = ws.cell(row=row, column=1).value
                if not ticker:
                    continue
                preco = float(ws.cell(row=row, column=4).value or 0.0)
                div = float(ws.cell(row=row, column=6).value or 0.0)
                feedback = str(ws.cell(row=row, column=13).value or "").strip()
                positions.append(Position(
                    ticker=str(ticker).strip().upper(),
                    row_index=row,
                    preco_atual=preco,
                    div_proj=div,
                    feedback_usuario=feedback
                ))
            wb.close()
            return positions

        return await asyncio.to_thread(_load)

    async def update_positions(self, positions: List[Position]) -> None:
        def _update():
            if not os.path.exists(self.file_path):
                return
            wb = openpyxl.load_workbook(self.file_path)
            ws = wb["Dashboard Barsi"]
            for p in positions:
                row = p.row_index
                # Coluna D: Preço
                ws.cell(row=row, column=4, value=p.preco_atual)
                # Coluna I: Status
                cell_status = ws.cell(row=row, column=9, value=p.status_barsi.value)
                cell_status.alignment = Alignment(horizontal="center", vertical="center")
                # Coluna L: Insight
                cell_insight = ws.cell(row=row, column=12, value=p.insight_atual)
                cell_insight.font = Font(name="Segoe UI", size=9, italic=True)
                cell_insight.alignment = Alignment(vertical="center", wrap_text=True)

            wb.save(self.file_path)
            wb.close()
            logger.info("xlsx_updated_successfully", path=self.file_path)

        await asyncio.to_thread(_update)

