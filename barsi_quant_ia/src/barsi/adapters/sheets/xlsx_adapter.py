"""Adapter para Excel Local (.xlsx) usando openpyxl com suporte ao layout B.E.S.T."""
import os
import asyncio
from typing import List
from barsi.ports.sheets import SheetsPort
from barsi.domain.models import Position, AcaoBarsi, SimuladorOrder
from barsi.infra.logging import logger
import openpyxl
from openpyxl.styles import Font, Alignment

class XLSXAdapter(SheetsPort):
    def __init__(self, file_path: str = "carteira_barsi_quant_ia.xlsx"):
        candidate_paths = [
            file_path,
            os.path.join("barsi_quant_ia", file_path),
            os.path.join(os.path.dirname(__file__), "../../../carteira_barsi_quant_ia.xlsx"),
            os.path.join(os.path.dirname(__file__), "../../../barsi_quant_ia/carteira_barsi_quant_ia.xlsx"),
        ]
        self.file_path = file_path
        for cp in candidate_paths:
            if os.path.exists(cp):
                self.file_path = cp
                break

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
            if not os.path.exists(self.file_path):
                logger.error("xlsx_not_found", path=self.file_path)
                return []
            wb = openpyxl.load_workbook(self.file_path, data_only=True)
            sheet_names = wb.sheetnames
            
            # Cenário 1: Screener_BEST_Custodia
            if "Screener_BEST_Custodia" in sheet_names:
                ws = wb["Screener_BEST_Custodia"]
                positions = []
                for row in range(10, 20):
                    ticker = ws.cell(row=row, column=1).value
                    if not ticker:
                        continue
                    setor = str(ws.cell(row=row, column=2).value or "").strip()
                    empresa = str(ws.cell(row=row, column=3).value or "").strip()
                    qtd_antes = int(float(ws.cell(row=row, column=4).value or 0))
                    qtd_aporte = int(float(ws.cell(row=row, column=5).value or 0))
                    qtd_total = int(float(ws.cell(row=row, column=6).value or (qtd_antes + qtd_aporte)))
                    preco = float(ws.cell(row=row, column=8).value or ws.cell(row=row, column=7).value or 0.0)
                    div = float(ws.cell(row=row, column=9).value or 0.0)
                    teto = float(ws.cell(row=row, column=10).value or (div / 0.06 if div else 0.0))
                    margem = float(ws.cell(row=row, column=11).value or 0.0)
                    dy = float(ws.cell(row=row, column=12).value or 0.0)
                    val_tot = float(ws.cell(row=row, column=13).value or (qtd_total * preco))
                    tot_ap = float(ws.cell(row=row, column=15).value or (qtd_aporte * preco))
                    status_str = str(ws.cell(row=row, column=16).value or "AGUARDAR").strip()
                    val_q = str(ws.cell(row=row, column=17).value or "").strip().upper()
                    teto_manual = val_q in ["SIM", "TRUE", "1", "S"]

                    positions.append(Position(
                        ticker=str(ticker).strip().upper(),
                        row_index=row,
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
                        valor_total=val_tot,
                        total_aporte=tot_ap,
                        status_barsi=AcaoBarsi.ACUMULAR if "ACUMULAR" in status_str else AcaoBarsi.AGUARDAR,
                        teto_manual=teto_manual
                    ))
                wb.close()
                return positions

            # Cenário 2: Dashboard Barsi (Legado)
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

    async def load_simulador_orders(self) -> List[SimuladorOrder]:
        def _load_sim():
            if not os.path.exists(self.file_path):
                return []
            wb = openpyxl.load_workbook(self.file_path, data_only=True)
            if "Simulador_Aportes_Barsi" not in wb.sheetnames:
                wb.close()
                return []
            ws = wb["Simulador_Aportes_Barsi"]
            orders = []
            for row in range(12, 16):
                rank_val = ws.cell(row=row, column=1).value
                if not rank_val or not str(rank_val).strip().isdigit():
                    continue
                rank = int(rank_val)
                ticker = str(ws.cell(row=row, column=2).value or "").strip().upper()
                empresa = str(ws.cell(row=row, column=3).value or "").strip()
                setor = str(ws.cell(row=row, column=4).value or "").strip()
                cotacao = float(ws.cell(row=row, column=5).value or 0.0)
                teto = float(ws.cell(row=row, column=6).value or 0.0)
                margem = float(ws.cell(row=row, column=7).value or 0.0)
                aloc = float(ws.cell(row=row, column=8).value or 0.0)
                orc = float(ws.cell(row=row, column=9).value or 0.0)
                qtd_frac = int(float(ws.cell(row=row, column=10).value or 0))
                gasto = float(ws.cell(row=row, column=11).value or 0.0)
                troco = float(ws.cell(row=row, column=12).value or 0.0)
                prov = float(ws.cell(row=row, column=13).value or 0.0)
                yd = float(ws.cell(row=row, column=14).value or 0.0)
                status = str(ws.cell(row=row, column=15).value or "").strip()
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
            wb.close()
            return orders

        return await asyncio.to_thread(_load_sim)

    async def update_positions(self, positions: List[Position], invalidate: bool = True) -> None:
        def _update():
            if not os.path.exists(self.file_path):
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.title = "Screener_BEST_Custodia"
                headers = [
                    "Ticker", "Setor", "Empresa", "Qtd Antes", "Qtd Aporte",
                    "Qtd Total", "Preço Médio", "Preço Atual", "Div Proj",
                    "Preço Teto", "Margem Seg", "DY", "Valor Total", "Meta %",
                    "Total Aporte", "Status", "Teto Manual"
                ]
                for col_idx, h in enumerate(headers, start=1):
                    ws.cell(row=9, column=col_idx, value=h)
                for idx, p in enumerate(positions):
                    r = p.row_index if (p.row_index and p.row_index >= 10) else (10 + idx)
                    ws[f"A{r}"] = p.ticker
                    ws[f"B{r}"] = p.setor
                    ws[f"C{r}"] = p.empresa
                wb.save(self.file_path)
                wb.close()

            wb = openpyxl.load_workbook(self.file_path)
            sheet_names = wb.sheetnames
            if "Screener_BEST_Custodia" in sheet_names:
                ws = wb["Screener_BEST_Custodia"]
                for p in positions:
                    r = p.row_index
                    qtd_tot = p.qtd_antes + p.qtd_aporte
                    if not ws[f"A{r}"].value:
                        ws[f"A{r}"] = p.ticker
                    if not ws[f"B{r}"].value:
                        ws[f"B{r}"] = p.setor
                    if not ws[f"C{r}"].value:
                        ws[f"C{r}"] = p.empresa
                    ws[f"D{r}"] = p.qtd_antes
                    ws[f"E{r}"] = p.qtd_aporte
                    ws[f"F{r}"] = qtd_tot
                    ws[f"H{r}"] = round(p.preco_atual, 2)
                    ws[f"I{r}"] = round(p.div_proj, 2)
                    ws[f"J{r}"] = round(p.preco_teto, 2)
                    ws[f"K{r}"] = round(p.margem_seguranca / 100.0, 4)
                    ws[f"M{r}"] = round(qtd_tot * p.preco_atual, 2)
                    ws[f"O{r}"] = round(p.qtd_aporte * p.preco_atual, 2)
                    ws[f"P{r}"] = p.status_barsi.value
                    ws[f"Q{r}"] = "SIM" if getattr(p, "teto_manual", False) else "NÃO"
            elif "Dashboard Barsi" in sheet_names:
                ws = wb["Dashboard Barsi"]
                for p in positions:
                    row = p.row_index
                    ws.cell(row=row, column=4, value=p.preco_atual)
                    ws.cell(row=row, column=6, value=p.div_proj)
                    cell_status = ws.cell(row=row, column=9, value=p.status_barsi.value)
                    cell_status.alignment = Alignment(horizontal="center", vertical="center")
                    cell_insight = ws.cell(row=row, column=12, value=p.insight_atual)
                    cell_insight.font = Font(name="Segoe UI", size=9, italic=True)
                    cell_insight.alignment = Alignment(vertical="center", wrap_text=True)

            wb.save(self.file_path)
            wb.close()
            logger.info("xlsx_updated_successfully", path=self.file_path)

        await asyncio.to_thread(_update)

