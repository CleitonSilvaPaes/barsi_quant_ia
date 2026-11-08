"""Testes para SyncHealthService (Auditoria e Reconciliação Unificada de Fontes)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from barsi.config import Settings
from barsi.domain.models import Position, AcaoBarsi
from barsi.services.sync_health_service import SyncHealthService
from barsi.adapters.history.budget_repository import BudgetRepository

@pytest.fixture
def mock_settings(tmp_path):
    db_file = str(tmp_path / "sync_health_test.db")
    return Settings(sqlite_db_path=db_file)

@pytest.fixture
def sample_positions():
    p1 = Position(
        ticker="BBAS3", row_index=10, preco_atual=17.13, div_proj=2.30,
        preco_teto=38.33, qtd_antes=100, qtd_aporte=10, status_barsi=AcaoBarsi.ACUMULAR
    )
    p2 = Position(
        ticker="SAPR4", row_index=11, preco_atual=6.29, div_proj=0.58,
        preco_teto=9.67, qtd_antes=500, qtd_aporte=50, status_barsi=AcaoBarsi.ACUMULAR
    )
    return [p1, p2]

@pytest.mark.asyncio
async def test_audit_sources_all_in_sync(mock_settings, sample_positions, monkeypatch):
    service = SyncHealthService(mock_settings)
    
    # Salva no SQLite
    repo = BudgetRepository(mock_settings.sqlite_db_path)
    repo.save_positions(sample_positions)

    # Mock XLSX
    mock_xlsx = MagicMock()
    mock_xlsx.load_positions = AsyncMock(return_value=[p.model_copy() for p in sample_positions])
    monkeypatch.setattr("barsi.services.sync_health_service.os.path.exists", lambda path: True)
    monkeypatch.setattr("barsi.services.sync_health_service.XLSXAdapter", lambda path: mock_xlsx)

    # Mock Sheets
    mock_sheets = MagicMock()
    mock_sheets.load_positions = AsyncMock(return_value=[p.model_copy() for p in sample_positions])
    monkeypatch.setattr("barsi.services.sync_health_service.AdapterFactory.create_sheets_adapter", lambda s: mock_sheets)

    report = await service.audit_sources()
    assert report["in_sync"] is True
    assert report["tickers_checked"] == 2
    assert len(report["divergences"]) == 0
    assert report["sources"]["sqlite"]["count"] == 2
    assert report["sources"]["xlsx"]["count"] == 2
    assert report["sources"]["sheets"]["count"] == 2

@pytest.mark.asyncio
async def test_audit_sources_detects_value_divergence(mock_settings, sample_positions, monkeypatch):
    service = SyncHealthService(mock_settings)
    
    # SQLite tem 100 cotas de BBAS3
    repo = BudgetRepository(mock_settings.sqlite_db_path)
    repo.save_positions(sample_positions)

    # XLSX tem 100 cotas
    mock_xlsx = MagicMock()
    mock_xlsx.load_positions = AsyncMock(return_value=[p.model_copy() for p in sample_positions])
    monkeypatch.setattr("barsi.services.sync_health_service.os.path.exists", lambda path: True)
    monkeypatch.setattr("barsi.services.sync_health_service.XLSXAdapter", lambda path: mock_xlsx)

    # Sheets tem 150 cotas de BBAS3 (divergência!)
    divergent_sheets = [
        sample_positions[0].model_copy(update={"qtd_antes": 150}),
        sample_positions[1].model_copy()
    ]
    mock_sheets = MagicMock()
    mock_sheets.load_positions = AsyncMock(return_value=divergent_sheets)
    monkeypatch.setattr("barsi.services.sync_health_service.AdapterFactory.create_sheets_adapter", lambda s: mock_sheets)

    report = await service.audit_sources()
    assert report["in_sync"] is False
    assert len(report["divergences"]) >= 1
    bbas3_div = next(d for d in report["divergences"] if d["ticker"] == "BBAS3")
    field_diffs = {d["field"]: d["values"] for d in bbas3_div["divergences"]}
    assert "qtd_antes" in field_diffs
    assert field_diffs["qtd_antes"]["sheets"] == 150
    assert field_diffs["qtd_antes"]["sqlite"] == 100

@pytest.mark.asyncio
async def test_audit_sources_handles_source_error(mock_settings, sample_positions, monkeypatch):
    service = SyncHealthService(mock_settings)

    repo = BudgetRepository(mock_settings.sqlite_db_path)
    repo.save_positions(sample_positions)

    mock_xlsx = MagicMock()
    mock_xlsx.load_positions = AsyncMock(return_value=[p.model_copy() for p in sample_positions])
    monkeypatch.setattr("barsi.services.sync_health_service.os.path.exists", lambda path: True)
    monkeypatch.setattr("barsi.services.sync_health_service.XLSXAdapter", lambda path: mock_xlsx)

    # Sheets falha com exceção de rede/autenticação
    mock_sheets = MagicMock()
    mock_sheets.load_positions = AsyncMock(side_effect=RuntimeError("Sheets API quota exceeded"))
    monkeypatch.setattr("barsi.services.sync_health_service.AdapterFactory.create_sheets_adapter", lambda s: mock_sheets)

    report = await service.audit_sources()
    assert report["in_sync"] is False
    assert report["sources"]["sheets"]["available"] is False
    assert "Sheets API quota exceeded" in report["sources"]["sheets"]["error"]

@pytest.mark.asyncio
async def test_reconcile_sources_master_sheets(mock_settings, sample_positions, monkeypatch):
    service = SyncHealthService(mock_settings)

    # Sheets tem 2 posições canônicas
    mock_sheets = MagicMock()
    mock_sheets.load_positions = AsyncMock(return_value=[p.model_copy() for p in sample_positions])
    monkeypatch.setattr("barsi.services.sync_health_service.AdapterFactory.create_sheets_adapter", lambda s: mock_sheets)

    # XLSX mock
    mock_xlsx = MagicMock()
    mock_xlsx.update_positions = AsyncMock()
    monkeypatch.setattr("barsi.services.sync_health_service.os.path.exists", lambda path: True)
    monkeypatch.setattr("barsi.services.sync_health_service.XLSXAdapter", lambda path: mock_xlsx)

    res = await service.reconcile_sources(master="sheets")
    assert res["ok"] is True
    assert res["master_source"] == "sheets"
    assert "sqlite" in res["synced_targets"]
    assert "xlsx" in res["synced_targets"]
    assert res["positions_count"] == 2

    # Verifica que o SQLite foi realmente atualizado
    repo = BudgetRepository(mock_settings.sqlite_db_path)
    saved = repo.load_positions()
    assert len(saved) == 2
    assert {p.ticker for p in saved} == {"BBAS3", "SAPR4"}

@pytest.mark.asyncio
async def test_reconcile_sources_master_sqlite(mock_settings, sample_positions, monkeypatch):
    service = SyncHealthService(mock_settings)

    # SQLite populado
    repo = BudgetRepository(mock_settings.sqlite_db_path)
    repo.save_positions(sample_positions)

    # XLSX mock
    mock_xlsx = MagicMock()
    mock_xlsx.update_positions = AsyncMock()
    monkeypatch.setattr("barsi.services.sync_health_service.os.path.exists", lambda path: True)
    monkeypatch.setattr("barsi.services.sync_health_service.XLSXAdapter", lambda path: mock_xlsx)

    # Sheets mock que receberá update_positions
    mock_sheets = MagicMock()
    mock_sheets.update_positions = AsyncMock()
    monkeypatch.setattr("barsi.services.sync_health_service.AdapterFactory.create_sheets_adapter", lambda s: mock_sheets)

    res = await service.reconcile_sources(master="sqlite")
    assert res["ok"] is True
    assert res["master_source"] == "sqlite"
    assert "xlsx" in res["synced_targets"]
    assert "sheets" in res["synced_targets"]
    mock_sheets.update_positions.assert_awaited_once()
    mock_xlsx.update_positions.assert_awaited_once()

@pytest.mark.asyncio
async def test_reconcile_sources_empty_master(mock_settings, monkeypatch):
    service = SyncHealthService(mock_settings)

    # SQLite vazio
    res = await service.reconcile_sources(master="sqlite")
    assert res["ok"] is False
    assert "Nenhuma posição encontrada" in res["detail"]
