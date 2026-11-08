"""Testes para GSpreadAdapter.sync_budget_manager (Sincronização Orçamentária Dinâmica)."""
import pytest
from unittest.mock import MagicMock
from barsi.adapters.sheets.gspread_adapter import GSpreadAdapter
from barsi.services.ofx_service import ResumoExtratoMes

class FakeWorksheet:
    def __init__(self, title="Budget_Manager_Fluxo", rows=None, col_count=10):
        self.title = title
        self.id = 12345
        self._rows = rows if rows is not None else [[""] * col_count for _ in range(15)]
        self.col_count = col_count
        self.added_cols = []
        self.deleted_cols = []
        self.updates = []
        self.batch_updates = []

    def get_all_values(self):
        return self._rows

    def add_cols(self, n):
        self.added_cols.append(n)
        self.col_count += n

    def delete_columns(self, start, end):
        self.deleted_cols.append((start, end))
        self.col_count -= (end - start + 1)

    def update(self, range_name, values, value_input_option=None):
        self.updates.append({"range": range_name, "values": values, "option": value_input_option})

    def batch_update(self, data, value_input_option=None):
        self.batch_updates.append({"data": data, "option": value_input_option})


class FakeSpreadsheet:
    def __init__(self, worksheets_list):
        self._worksheets = worksheets_list
        self.batch_merges = []

    def worksheets(self):
        return self._worksheets

    def worksheet(self, name):
        for ws in self._worksheets:
            if ws.title == name:
                return ws
        raise KeyError(name)

    def batch_update(self, body):
        self.batch_merges.append(body)


def test_sync_budget_manager_sheet_not_found(tmp_path, monkeypatch):
    test_db = str(tmp_path / "budget_test.db")
    adapter = GSpreadAdapter(credentials_path="mock.json", sqlite_db_path=test_db)
    
    fake_sh = FakeSpreadsheet([FakeWorksheet("OutraAba")])
    monkeypatch.setattr(adapter, "_get_client_and_spreadsheet", lambda: fake_sh)

    resumo = ResumoExtratoMes(mes_ano="2026-01", total_entradas=5000.0)
    # Não deve lançar erro
    adapter.sync_budget_manager({"2026-01": resumo})
    assert len(fake_sh.batch_merges) == 0


def test_sync_budget_manager_too_few_rows(tmp_path, monkeypatch):
    test_db = str(tmp_path / "budget_test.db")
    adapter = GSpreadAdapter(credentials_path="mock.json", sqlite_db_path=test_db)
    
    ws = FakeWorksheet("Budget_Manager_Fluxo", rows=[["row1"], ["row2"]])
    fake_sh = FakeSpreadsheet([ws])
    monkeypatch.setattr(adapter, "_get_client_and_spreadsheet", lambda: fake_sh)

    resumo = ResumoExtratoMes(mes_ano="2026-01", total_entradas=5000.0)
    adapter.sync_budget_manager({"2026-01": resumo})
    assert len(ws.updates) == 0
    assert len(ws.batch_updates) == 0


def test_sync_budget_manager_no_active_months(tmp_path, monkeypatch):
    test_db = str(tmp_path / "budget_test.db")
    adapter = GSpreadAdapter(credentials_path="mock.json", sqlite_db_path=test_db)
    
    ws = FakeWorksheet("Budget_Manager_Fluxo")
    fake_sh = FakeSpreadsheet([ws])
    monkeypatch.setattr(adapter, "_get_client_and_spreadsheet", lambda: fake_sh)

    # Todos com valores zerados
    resumo = ResumoExtratoMes(mes_ano="2026-01")
    adapter.sync_budget_manager({"2026-01": resumo})
    assert len(ws.updates) == 0
    assert len(ws.batch_updates) == 0


def test_sync_budget_manager_full_flow_with_two_months(tmp_path, monkeypatch):
    test_db = str(tmp_path / "budget_test.db")
    adapter = GSpreadAdapter(credentials_path="mock.json", sqlite_db_path=test_db)
    
    # Mock do repositório de despesas vigentes no SQLite
    mock_expenses = {
        "Aluguel": 1500.0,
        "Compromisso Familiar": 600.0,
        "DAS Simples Nacional": 75.0,
        "Licenciamento Detran": 0.0,
        "Cooperativa Ailos": 250.0,
        "Saldo Ant. Caixa": 1000.0,
        "Saldo Inicial": 2000.0,
        "Gastos Variáveis A": 100.0,
        "Gastos Variáveis B": 50.0,
        "Gastos Variáveis C": 0.0,
        "Gastos Variáveis D": 0.0,
        "Gastos Variáveis E": 0.0,
    }
    monkeypatch.setattr(adapter.budget_repo, "get_all_expenses", lambda m: mock_expenses)

    ws = FakeWorksheet("Budget_Manager_Fluxo", col_count=5)
    fake_sh = FakeSpreadsheet([ws])
    monkeypatch.setattr(adapter, "_get_client_and_spreadsheet", lambda: fake_sh)

    # Prepara 2 resumos mensais
    r1 = ResumoExtratoMes(
        mes_ano="2026-01",
        salario_entradas=7500.0,
        proventos_recebidos=120.0,
        faturas_cartao=1200.0,
        reserva_rdb=500.0,
        compras_acoes_barsi=1500.0,
        compras_fiis=500.0,
        total_entradas=7620.0,
        total_saidas=3700.0
    )
    r2 = ResumoExtratoMes(
        mes_ano="2026-02",
        salario_entradas=8000.0,
        proventos_recebidos=180.0,
        faturas_cartao=1100.0,
        reserva_rdb=600.0,
        compras_acoes_barsi=0.0,  # Zero para acionar o fallback SUMIFS
        compras_fiis=0.0,        # Zero para acionar o fallback SUMIFS
        total_entradas=8180.0,
        total_saidas=3500.0
    )

    resumos = {"2026-02": r2, "2026-01": r1} # Não ordenado propositalmente para testar ordenação
    adapter.sync_budget_manager(resumos)

    # 1. Verifica ajuste dinâmico de colunas
    # 2 meses: Item (1) + Mês1 (1) + Mês2 (1) + VarNominal (1) + %Orç (1) + Status (1) + Meio (1) = 7 colunas
    assert ws.col_count == 7
    assert len(fake_sh.batch_merges) == 1  # Fez batch_update de merges

    # 2. Verifica cabeçalhos na linha 9
    assert len(ws.updates) == 1
    header_update = ws.updates[0]
    assert header_update["range"] == "A9:G9"
    headers = header_update["values"][0]
    assert headers[0] == "Item / Descrição Orçamentária"
    assert headers[1] == "Janeiro/2026 (R$)"
    assert headers[2] == "Fevereiro/2026 (R$)"
    assert headers[3] == "Variação Nominal (R$)"
    assert "% Orçamento Fev" in headers[4]

    # 3. Verifica dados no batch_updates do worksheet
    assert len(ws.batch_updates) == 1
    batch_data = ws.batch_updates[0]["data"]
    data_map = {item["range"]: item["values"][0][0] for item in batch_data}

    # Mês 1 (Janeiro = coluna B)
    assert data_map["B11"] == 7500.0   # Salário
    assert data_map["B12"] == 120.0    # Proventos
    assert data_map["B13"] == 2000.0   # Saldo anterior (do SQLite)
    assert data_map["B14"] == "=SUM(B11:B13)"
    assert data_map["B17"] == 1200.0   # Fatura cartão
    assert data_map["B18"] == 1500.0   # Aluguel
    assert data_map["B31"] == 1500.0   # Ações compras
    assert data_map["B32"] == 500.0    # FIIs compras
    assert data_map["B33"] == "=SUM(B31:B32)"

    # Mês 2 (Fevereiro = coluna C)
    assert data_map["C11"] == 8000.0
    assert data_map["C12"] == 180.0
    # Como compras_acoes_barsi foi 0.0, deve usar a fórmula de fallback
    assert "SUMIFS(Screener_BEST_Custodia!O10:O19" in str(data_map["C31"])
    assert "<>*FII*" in str(data_map["C31"])
    assert "*FII*" in str(data_map["C32"])

    # Fórmulas de variação e percentual do último mês (coluna D = Variação C - B, coluna E = % C / C14)
    assert data_map["D11"] == "=C11-B11"
    assert data_map["E11"] == "=C11/C$14"

    # Cards superiores
    assert data_map["A4"] == "ENTRADAS TOTAIS (FEV/26)"
    assert data_map["A5"] == "=C14"
    assert data_map["E4"] == "SAÍDAS FIXAS OBRIGATÓRIAS"
    assert data_map["E5"] == "=C23"
    assert data_map["I4"] == "APORTES + RESERVA"
    assert data_map["I5"] == "=C27+C33"
    assert data_map["M4"] == "SALDO LIVRE RESIDUAL"
    assert data_map["M5"] == "=C45"

    # Título linha 8
    assert data_map["A8"] == "CONCILIAÇÃO ORÇAMENTÁRIA DETALHADA (JANEIRO A FEVEREIRO 2026)"


def test_budget_projection_service_col_idx_to_letter():
    from barsi.services.budget_projection_service import BudgetProjectionService
    assert BudgetProjectionService.col_idx_to_letter(1) == "A"
    assert BudgetProjectionService.col_idx_to_letter(26) == "Z"
    assert BudgetProjectionService.col_idx_to_letter(27) == "AA"
    assert BudgetProjectionService.col_idx_to_letter(28) == "AB"


def test_budget_projection_service_direct_sync_without_spreadsheet(tmp_path):
    from barsi.services.budget_projection_service import BudgetProjectionService
    from barsi.adapters.history.budget_repository import BudgetRepository
    test_db = str(tmp_path / "budget_direct.db")
    repo = BudgetRepository(test_db)
    service = BudgetProjectionService(budget_repo=repo)
    # Sem spreadsheet e sem adapter, deve retornar False graciosamente
    res = service.sync_budget_manager({})
    assert res is False

