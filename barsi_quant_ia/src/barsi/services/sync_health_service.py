"""Serviço de Auditoria e Reconciliação Unificada de Fontes (Sheets ↔ XLSX ↔ SQLite)."""
import os
from typing import Dict, Any, List, Optional
from barsi.config import Settings
from barsi.domain.models import Position
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.sheets.xlsx_adapter import XLSXAdapter
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.infra.logging import logger

class SyncHealthService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.sqlite_db_path = settings.sqlite_db_path
        self.xlsx_path = os.path.join(os.path.dirname(__file__), "../../../carteira_barsi_quant_ia.xlsx")
        if not os.path.exists(self.xlsx_path):
            self.xlsx_path = "carteira_barsi_quant_ia.xlsx"

    async def audit_sources(self) -> Dict[str, Any]:
        """Audita e compara linha a linha as 3 fontes: Google Sheets, Excel (.xlsx) e SQLite."""
        report = {
            "in_sync": True,
            "sources": {
                "sheets": {"available": False, "count": 0, "error": None},
                "xlsx": {"available": False, "count": 0, "error": None},
                "sqlite": {"available": False, "count": 0, "error": None},
            },
            "divergences": [],
            "tickers_checked": 0,
        }

        # 1. Carrega do SQLite
        sqlite_positions: Dict[str, Position] = {}
        try:
            repo = BudgetRepository(self.sqlite_db_path)
            rows = repo.load_positions()
            sqlite_positions = {p.ticker: p for p in rows}
            report["sources"]["sqlite"] = {"available": True, "count": len(rows), "error": None}
        except Exception as e:
            report["sources"]["sqlite"]["error"] = str(e)
            report["in_sync"] = False

        # 2. Carrega do XLSX local
        xlsx_positions: Dict[str, Position] = {}
        try:
            if os.path.exists(self.xlsx_path):
                xlsx_adapter = XLSXAdapter(self.xlsx_path)
                rows = await xlsx_adapter.load_positions()
                xlsx_positions = {p.ticker: p for p in rows}
                report["sources"]["xlsx"] = {"available": True, "count": len(rows), "error": None}
            else:
                report["sources"]["xlsx"]["error"] = f"Arquivo não encontrado em {self.xlsx_path}"
        except Exception as e:
            report["sources"]["xlsx"]["error"] = str(e)
            report["in_sync"] = False

        # 3. Carrega do Google Sheets
        sheets_positions: Dict[str, Position] = {}
        try:
            sheets_adapter = AdapterFactory.create_sheets_adapter(self.settings)
            rows = await sheets_adapter.load_positions()
            sheets_positions = {p.ticker: p for p in rows}
            report["sources"]["sheets"] = {"available": True, "count": len(rows), "error": None}
        except Exception as e:
            report["sources"]["sheets"]["error"] = str(e)
            report["in_sync"] = False

        all_tickers = sorted(list(set(list(sqlite_positions.keys()) + list(xlsx_positions.keys()) + list(sheets_positions.keys()))))
        report["tickers_checked"] = len(all_tickers)

        counts = [len(sqlite_positions), len(xlsx_positions), len(sheets_positions)]
        if len(set(counts)) > 1:
            report["in_sync"] = False

        for ticker in all_tickers:
            p_sheets = sheets_positions.get(ticker)
            p_xlsx = xlsx_positions.get(ticker)
            p_sqlite = sqlite_positions.get(ticker)

            diffs = []
            if not p_sheets or not p_xlsx or not p_sqlite:
                diffs.append({
                    "field": "presence",
                    "values": {
                        "sheets": bool(p_sheets),
                        "xlsx": bool(p_xlsx),
                        "sqlite": bool(p_sqlite)
                    }
                })

            # Compara Qtd Antes (Custódia)
            qtds = {
                "sheets": p_sheets.qtd_antes if p_sheets else None,
                "xlsx": p_xlsx.qtd_antes if p_xlsx else None,
                "sqlite": p_sqlite.qtd_antes if p_sqlite else None,
            }
            if not (p_sheets and p_xlsx and p_sqlite and (p_sheets.qtd_antes == p_xlsx.qtd_antes == p_sqlite.qtd_antes)):
                if p_sheets and p_xlsx and p_sqlite:
                    diffs.append({"field": "qtd_antes", "values": qtds})

            # Compara Aporte
            aportes = {
                "sheets": p_sheets.qtd_aporte if p_sheets else None,
                "xlsx": p_xlsx.qtd_aporte if p_xlsx else None,
                "sqlite": p_sqlite.qtd_aporte if p_sqlite else None,
            }
            if not (p_sheets and p_xlsx and p_sqlite and (p_sheets.qtd_aporte == p_xlsx.qtd_aporte == p_sqlite.qtd_aporte)):
                if p_sheets and p_xlsx and p_sqlite:
                    diffs.append({"field": "qtd_aporte", "values": aportes})

            # Compara Preço Teto
            tetos = {
                "sheets": round(p_sheets.preco_teto, 2) if p_sheets else None,
                "xlsx": round(p_xlsx.preco_teto, 2) if p_xlsx else None,
                "sqlite": round(p_sqlite.preco_teto, 2) if p_sqlite else None,
            }
            if not (p_sheets and p_xlsx and p_sqlite and (round(p_sheets.preco_teto, 2) == round(p_xlsx.preco_teto, 2) == round(p_sqlite.preco_teto, 2))):
                if p_sheets and p_xlsx and p_sqlite:
                    diffs.append({"field": "preco_teto", "values": tetos})

            if diffs:
                report["divergences"].append({
                    "ticker": ticker,
                    "divergences": diffs
                })
                report["in_sync"] = False

        return report

    async def reconcile_sources(self, master: str = "sheets") -> Dict[str, Any]:
        """Equaliza as 3 fontes (Sheets ↔ XLSX ↔ SQLite) usando uma fonte master oficial."""
        # 1. Obtém as posições canônicas
        if master == "sheets":
            adapter = AdapterFactory.create_sheets_adapter(self.settings)
            canonical_positions = await adapter.load_positions()
        elif master == "sqlite":
            repo = BudgetRepository(self.sqlite_db_path)
            canonical_positions = repo.load_positions()
        else:
            adapter = XLSXAdapter(self.xlsx_path)
            canonical_positions = await adapter.load_positions()

        if not canonical_positions:
            return {"ok": False, "detail": f"Nenhuma posição encontrada na fonte master '{master}'"}

        synced_targets = []

        # 2. Persiste no SQLite
        try:
            repo = BudgetRepository(self.sqlite_db_path)
            repo.save_positions(canonical_positions)
            synced_targets.append("sqlite")
        except Exception as e:
            logger.warn("reconcile_sqlite_error", error=str(e))

        # 3. Persiste no XLSX local
        try:
            if os.path.exists(self.xlsx_path):
                xlsx_adapter = XLSXAdapter(self.xlsx_path)
                await xlsx_adapter.update_positions(canonical_positions)
                synced_targets.append("xlsx")
        except Exception as e:
            logger.warn("reconcile_xlsx_error", error=str(e))

        # 4. Se o master não foi sheets, atualiza o Google Sheets
        if master != "sheets":
            try:
                sheets_adapter = AdapterFactory.create_sheets_adapter(self.settings)
                await sheets_adapter.update_positions(canonical_positions)
                synced_targets.append("sheets")
            except Exception as e:
                logger.warn("reconcile_sheets_error", error=str(e))

        return {
            "ok": True,
            "master_source": master,
            "synced_targets": synced_targets,
            "positions_count": len(canonical_positions),
            "tickers": [p.ticker for p in canonical_positions]
        }
