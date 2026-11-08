"""Testes unitários para o BudgetRepository e garantia de Zero Magic Numbers."""
import os
import re
import pathlib
import pytest
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position

def test_get_expense_amount_returns_vigente_value(tmp_path):
    db_file = str(tmp_path / "budget_test.db")
    repo = BudgetRepository(db_file)

    repo.upsert_recurring("Aluguel", 521.30, "2026-10-01")
    repo.upsert_recurring("Aluguel", 560.00, "2027-01-01")  # 1º reajuste
    repo.upsert_recurring("Aluguel", 600.00, "2028-01-01")  # 2º reajuste

    assert repo.get_expense_amount("2026-09", "Aluguel") == 0.0      # antes da vigência
    assert repo.get_expense_amount("2026-10", "Aluguel") == 521.30   # vigente período 1
    assert repo.get_expense_amount("2026-12", "Aluguel") == 521.30   # ainda vigente período 1
    assert repo.get_expense_amount("2027-06", "Aluguel") == 560.00   # vigente período 2
    assert repo.get_expense_amount("2028-03", "Aluguel") == 600.00   # vigente período 3

def test_upsert_closes_previous_valid_to(tmp_path):
    db_file = str(tmp_path / "budget_test.db")
    repo = BudgetRepository(db_file)

    repo.upsert_recurring("DAS", 86.05, "2026-10-01")
    repo.upsert_recurring("DAS", 95.00, "2026-12-01")

    rows = repo.list_recurring()
    das_rows = [r for r in rows if r["category"] == "DAS"]
    assert len(das_rows) == 2

    # O registro mais recente (2026-12-01) deve ser VIGENTE (valid_to is None)
    novo = [r for r in das_rows if r["valid_from"] == "2026-12-01"][0]
    assert novo["valid_to"] is None
    assert novo["amount"] == 95.00

    # O registro anterior (2026-10-01) deve ter valid_to fechado em 2026-11-30
    antigo = [r for r in das_rows if r["valid_from"] == "2026-10-01"][0]
    assert antigo["valid_to"] == "2026-11-30"
    assert antigo["amount"] == 86.05

def test_get_all_expenses_single_query(tmp_path):
    db_file = str(tmp_path / "budget_test.db")
    repo = BudgetRepository(db_file)

    repo.upsert_recurring("Aluguel", 521.30, "2026-10-01")
    repo.upsert_recurring("DAS", 86.05, "2026-10-01")
    repo.upsert_recurring("Licenciamento", 149.37, "2026-11-01")

    exp_out = repo.get_all_expenses("2026-10")
    assert exp_out.get("Aluguel") == 521.30
    assert exp_out.get("DAS") == 86.05
    assert "Licenciamento" not in exp_out

    exp_nov = repo.get_all_expenses("2026-11")
    assert exp_nov.get("Aluguel") == 521.30
    assert exp_nov.get("DAS") == 86.05
    assert exp_nov.get("Licenciamento") == 149.37

def test_persist_transacoes_idempotency(tmp_path):
    db_file = str(tmp_path / "budget_test.db")
    repo = BudgetRepository(db_file)

    txs = [
        {
            "id_transacao": "tx-001",
            "data": "2026-11-06",
            "tipo": "DEBIT",
            "valor": -312.40,
            "descricao": "Compra de BBAS3",
            "categoria": "Aporte Ações/FIIs",
            "ticker_relacionado": "BBAS3",
            "origem": "CONTA",
        },
        {
            "id_transacao": "tx-002",
            "data": "2026-11-06",
            "tipo": "CREDIT",
            "valor": 4340.50,
            "descricao": "Salário",
            "categoria": "Salário / Renda Principal",
            "origem": "CONTA",
        }
    ]

    ins1 = repo.persist_transacoes(txs)
    assert ins1 == 2

    # Inserção idêntica subsequente deve ser ignorada (idempotência por fitid, tipo, valor)
    ins2 = repo.persist_transacoes(txs)
    assert ins2 == 0

def test_persist_and_load_positions(tmp_path):
    db_file = str(tmp_path / "budget_test.db")
    repo = BudgetRepository(db_file)

    pos = [
        Position(
            ticker="BBAS3",
            row_index=10,
            empresa="Banco do Brasil",
            setor="Financeiro",
            qtd_antes=100,
            qtd_aporte=10,
            preco_atual=27.50,
            div_proj=2.30,
            preco_teto=38.33,
            margem_seguranca=39.38,
            feedback_usuario="Aportar com foco no DY",
            insight_atual="Forte margem de segurança",
        )
    ]

    repo.persist_positions(pos)
    loaded = repo.load_positions()
    assert len(loaded) == 1
    assert loaded[0].ticker == "BBAS3"
    assert loaded[0].margem_seguranca == 39.38
    assert loaded[0].empresa == "Banco do Brasil"

def test_no_hardcoded_values_in_sync_budget_manager():
    """Falha o teste se alguém reintroduzir `if m == 'YYYY-MM'` em gspread_adapter.py."""
    candidates = [
        pathlib.Path("src/barsi/adapters/sheets/gspread_adapter.py"),
        pathlib.Path("barsi_quant_ia/src/barsi/adapters/sheets/gspread_adapter.py"),
    ]
    target_file = None
    for c in candidates:
        if c.exists():
            target_file = c
            break

    assert target_file is not None, "Arquivo gspread_adapter.py não encontrado"
    src = target_file.read_text(encoding="utf-8")
    
    match = re.search(r'if\s+m\s+(?:==|in)\s+["\']20\d{2}-\d{2}', src)
    assert match is None, f"Valor hardcoded por mês detectado em gspread_adapter.py: '{match.group(0) if match else ''}'"


def test_upsert_recurring_is_idempotent(tmp_path):
    """Garante que chamar upsert_recurring múltiplas vezes com mesmos parâmetros não duplica."""
    db_file = str(tmp_path / "idempotent.db")
    repo = BudgetRepository(db_file)

    repo.upsert_recurring("Aluguel", 521.30, "2026-10-01")
    repo.upsert_recurring("Aluguel", 521.30, "2026-10-01")  # Mesma chamada

    rows = repo.list_recurring()
    aluguel_rows = [r for r in rows if r["category"] == "Aluguel"]
    assert len(aluguel_rows) == 1
    assert aluguel_rows[0]["amount"] == 521.30


def test_budget_entries_override_recurring(tmp_path):
    """Garante que entradas conciliadas em budget_entries sobrepõem o valor estimado recorrente."""
    db_file = str(tmp_path / "override.db")
    repo = BudgetRepository(db_file)

    # Recorrente padrão
    repo.upsert_recurring("Aluguel", 500.00, "2026-10-01")

    # Sem snapshot, retorna recorrente
    assert repo.get_expense_amount("2026-11", "Aluguel") == 500.00

    # Insere snapshot consolidado em budget_entries para 2026-11
    repo.record_budget_entry("2026-11", "Aluguel", 521.30, source="manual")

    # Com snapshot, deve priorizar budget_entries (521.30)
    assert repo.get_expense_amount("2026-11", "Aluguel") == 521.30
    all_exp = repo.get_all_expenses("2026-11")
    assert all_exp.get("Aluguel") == 521.30


def test_processar_diretorio_nao_toca_sqlite(tmp_path, monkeypatch):
    """Garante que processar_diretorio_extratos é uma função pura e não cria banco SQLite."""
    from barsi.services.ofx_service import OFXParserService

    ofx_sample = """OFXHEADER:100
<OFX>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>CREDIT</TRNTYPE>
<DTPOSTED>20261106000000[-3:BRT]</DTPOSTED>
<TRNAMT>100.00</TRNAMT>
<FITID>pure-01</FITID>
<MEMO>PIX TESTE</MEMO>
</STMTTRN>
</BANKTRANLIST>
</OFX>"""
    ofx_dir = tmp_path / "extratos"
    ofx_dir.mkdir()
    (ofx_dir / "teste.ofx").write_text(ofx_sample, encoding="utf-8")

    db_target = tmp_path / "should_not_exist.db"
    monkeypatch.chdir(tmp_path)

    # Executa parser
    res = OFXParserService.processar_diretorio_extratos(str(ofx_dir))
    assert "2026-11" in res

    # Não deve existir arquivo de banco no diretório temporário nem em ./memory
    assert not db_target.exists()
    assert not (tmp_path / "memory").exists()

