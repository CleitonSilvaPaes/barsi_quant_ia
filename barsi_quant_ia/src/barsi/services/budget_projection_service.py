"""Serviço de Domínio para Projeção Orçamentária e Sincronização Dinâmica da Aba Budget_Manager_Fluxo."""
from typing import Dict, Any, Optional
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.services.ofx_service import ResumoExtratoMes
from barsi.infra.logging import logger

class BudgetProjectionService:
    """Encapsula a inteligência de conciliação e projeção orçamentária para planilhas e relatórios."""

    def __init__(self, budget_repo: BudgetRepository, sheets_adapter: Any = None):
        self.budget_repo = budget_repo
        self.sheets_adapter = sheets_adapter

    @staticmethod
    def col_idx_to_letter(idx: int) -> str:
        """Converte índice numérico (1, 2, 3...) em letra de coluna Excel/Sheets (A, B, C...)."""
        string = ""
        while idx > 0:
            idx, remainder = divmod(idx - 1, 26)
            string = chr(65 + remainder) + string
        return string

    def sync_budget_manager(self, resumos: Dict[str, ResumoExtratoMes], spreadsheet: Any = None) -> bool:
        """Sincroniza os valores de conciliação de extratos com a aba Budget_Manager_Fluxo de forma 100% dinâmica e cronológica."""
        sh = spreadsheet
        if sh is None and self.sheets_adapter is not None:
            if hasattr(self.sheets_adapter, "_get_client_and_spreadsheet"):
                sh = self.sheets_adapter._get_client_and_spreadsheet()

        if sh is None:
            logger.warning("budget_manager_spreadsheet_not_provided")
            return False

        worksheet_titles = [w.title for w in sh.worksheets()]
        if "Budget_Manager_Fluxo" not in worksheet_titles:
            logger.warning("budget_manager_sheet_not_found")
            return False

        ws = sh.worksheet("Budget_Manager_Fluxo")
        all_rows = ws.get_all_values()
        if len(all_rows) < 10:
            return False

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
            return False

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
        range_h = f"A9:{self.col_idx_to_letter(total_cols)}9"
        ws.update(range_name=range_h, values=[headers], value_input_option="USER_ENTERED")

        updates = []

        # Preenche colunas de cada mês cronológico (Linhas 11 a 45)
        for col_idx, m in enumerate(meses_ordenados, start=2):
            c_let = self.col_idx_to_letter(col_idx)
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
            if acoes > 0:
                updates.append({"range": f"{c_let}31", "values": [[acoes]]})
            else:
                updates.append({"range": f"{c_let}31", "values": [[f'=SUMIFS(Screener_BEST_Custodia!O10:O19; Screener_BEST_Custodia!B10:B19; "<>*FII*"; Screener_BEST_Custodia!P10:P19; "ACUMULAR")']]})

            if fiis > 0:
                updates.append({"range": f"{c_let}32", "values": [[fiis]]})
            else:
                updates.append({"range": f"{c_let}32", "values": [[f'=SUMIFS(Screener_BEST_Custodia!O10:O19; Screener_BEST_Custodia!B10:B19; "*FII*"; Screener_BEST_Custodia!P10:P19; "ACUMULAR")']]})
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
        penultimo_col = self.col_idx_to_letter(1 + len(meses_ordenados) - 1)
        ultimo_col = self.col_idx_to_letter(1 + len(meses_ordenados))
        var_col = self.col_idx_to_letter(1 + len(meses_ordenados) + 1)
        pct_col = self.col_idx_to_letter(1 + len(meses_ordenados) + 2)

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
        return True
