"""Adapter para Google Sheets (gspread) com detecção inteligente de layout."""
import os
import asyncio
import time
from typing import List, Optional
from datetime import datetime
import gspread

from barsi.ports.sheets import SheetsPort
from barsi.domain.models import Position, AcaoBarsi, SimuladorOrder
from barsi.services.budget_projection_service import BudgetProjectionService
from barsi.infra.logging import logger

class GSpreadAdapter(SheetsPort):
    _positions_cache: Optional[List[Position]] = None
    _positions_cache_time: float = 0.0
    _sim_orders_cache: Optional[List[SimuladorOrder]] = None
    _sim_orders_cache_time: float = 0.0
    _CACHE_TTL: float = 60.0  # Default TTL (configurável via BARSI_SHEETS_CACHE_TTL_SEC)

    @classmethod
    def invalidate_cache(cls):
        cls._positions_cache = None
        cls._positions_cache_time = 0.0
        cls._sim_orders_cache = None
        cls._sim_orders_cache_time = 0.0

    def __init__(self, credentials_path: str, spreadsheet_id: str = "", sqlite_db_path: str = "./memory/barsi_memory.db"):
        self.credentials_path = credentials_path
        self.spreadsheet_id = spreadsheet_id
        self.sqlite_db_path = sqlite_db_path
        self.cache_ttl = float(os.getenv("BARSI_SHEETS_CACHE_TTL_SEC", "60.0"))
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
        now = time.time()
        ttl = self.cache_ttl
        if GSpreadAdapter._positions_cache:
            elapsed = now - GSpreadAdapter._positions_cache_time
            if elapsed < ttl:
                ttl_rem = round(ttl - elapsed, 1)
                logger.info("sheets_cache", hit=True, ttl_remaining=ttl_rem)
                return GSpreadAdapter._positions_cache

        logger.info("sheets_cache", hit=False, ttl_remaining=0.0)

        def _load():
            try:
                sh = self._get_client_and_spreadsheet()
                worksheet_titles = [w.title for w in sh.worksheets()]
            except Exception as e:
                logger.warn("sheets_connect_error_fallback", error=str(e))
                if GSpreadAdapter._positions_cache:
                    return GSpreadAdapter._positions_cache
                # Consulta cache persistente em SQLite
                cached_positions, is_stale = self.budget_repo.get_sheets_positions_cache()
                if cached_positions:
                    if is_stale:
                        logger.warning("sheets_cache_stale", hint="Usando cache SQLite persistente do Google Sheets")
                    else:
                        logger.info("sheets_cache_sqlite_hit", count=len(cached_positions))
                    GSpreadAdapter._positions_cache = cached_positions
                    GSpreadAdapter._positions_cache_time = time.time()
                    return cached_positions
                return self.budget_repo.load_positions()

            # Cenário 1: Planilha Pro B.E.S.T. ('Screener_BEST_Custodia')
            if "Screener_BEST_Custodia" in worksheet_titles:
                ws = sh.worksheet("Screener_BEST_Custodia")
                rows = ws.get("A10:Q19")

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
                    teto_manual = False
                    if len(r) > 16 and r[16]:
                        teto_manual = str(r[16]).strip().upper() in ["SIM", "TRUE", "1", "S"]

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
                        feedback_usuario=fb,
                        teto_manual=teto_manual
                    ))
                GSpreadAdapter._positions_cache = positions
                GSpreadAdapter._positions_cache_time = time.time()
                try:
                    self.budget_repo.save_sheets_positions_cache(positions, ttl_seconds=int(self.cache_ttl))
                except Exception as e:
                    logger.warn("sheets_save_cache_sqlite_error", error=str(e))
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
            GSpreadAdapter._positions_cache = positions
            GSpreadAdapter._positions_cache_time = time.time()
            try:
                self.budget_repo.save_sheets_positions_cache(positions, ttl_seconds=int(self.cache_ttl))
            except Exception as e:
                logger.warn("sheets_save_cache_sqlite_error", error=str(e))
            return positions

        return await asyncio.to_thread(_load)

    async def load_simulador_orders(self) -> List[SimuladorOrder]:
        """Carrega as ordens programadas do Simulador de Aportes B.E.S.T."""
        now = time.time()
        if GSpreadAdapter._sim_orders_cache and (now - GSpreadAdapter._sim_orders_cache_time < GSpreadAdapter._CACHE_TTL):
            return GSpreadAdapter._sim_orders_cache

        def _load_sim():
            try:
                sh = self._get_client_and_spreadsheet()
                worksheet_titles = [w.title for w in sh.worksheets()]
            except Exception as e:
                logger.warn("sheets_connect_sim_fallback", error=str(e))
                if GSpreadAdapter._sim_orders_cache:
                    return GSpreadAdapter._sim_orders_cache
                return []

            if "Simulador_Aportes_Barsi" not in worksheet_titles:
                return []
            try:
                ws = sh.worksheet("Simulador_Aportes_Barsi")
                rows = ws.get("A12:O16")
            except Exception as e:
                logger.warn("sheets_get_sim_rows_fallback", error=str(e))
                if GSpreadAdapter._sim_orders_cache:
                    return GSpreadAdapter._sim_orders_cache
                return []

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
            GSpreadAdapter._sim_orders_cache = orders
            GSpreadAdapter._sim_orders_cache_time = time.time()
            return orders

        return await asyncio.to_thread(_load_sim)

    async def update_positions(self, positions: List[Position], invalidate: bool = True) -> None:
        if len(positions) == 1 and not invalidate:
            # Update parcial (ex: feedback individual): atualiza em memória sem invalidar o cache completo
            if GSpreadAdapter._positions_cache:
                for updated_p in positions:
                    for idx, cur_p in enumerate(GSpreadAdapter._positions_cache):
                        if cur_p.ticker == updated_p.ticker:
                            GSpreadAdapter._positions_cache[idx] = updated_p
        elif len(positions) >= 2 or invalidate:
            GSpreadAdapter.invalidate_cache()

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
                    # Col Q: Teto Manual ("SIM" ou "NÃO")
                    teto_val = "SIM" if getattr(p, "teto_manual", False) else "NÃO"
                    updates.append({"range": f"Q{row}", "values": [[teto_val]]})
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
        return BudgetProjectionService.col_idx_to_letter(idx)

    def sync_budget_manager(self, resumos: dict):
        """Sincroniza os valores de conciliação de extratos com a aba Budget_Manager_Fluxo delegando para o BudgetProjectionService."""
        service = BudgetProjectionService(budget_repo=self.budget_repo, sheets_adapter=self)
        return service.sync_budget_manager(resumos)

