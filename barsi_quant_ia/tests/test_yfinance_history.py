"""Testes para sync_yfinance_history (Histórico Diário, Timeout, Resiliência e Idempotência)."""
import pytest
import pandas as pd
from unittest.mock import MagicMock
from barsi.adapters.history.budget_repository import BudgetRepository

def create_fake_df(dates, closes, volumes):
    idx = pd.to_datetime(dates)
    return pd.DataFrame({"Close": closes, "Volume": volumes}, index=idx)

def test_sync_yfinance_history_success(tmp_path, monkeypatch):
    db_path = str(tmp_path / "yf_test.db")
    repo = BudgetRepository(db_path)

    fake_df = create_fake_df(
        dates=["2026-01-10", "2026-01-11", "2026-01-12"],
        closes=[17.10, 17.25, 17.50],
        volumes=[1000000, 1200000, 1100000]
    )

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = fake_df

    mock_yf = MagicMock()
    mock_yf.Ticker.return_value = mock_ticker
    monkeypatch.setattr("yfinance.Ticker", mock_yf.Ticker)

    records = repo.sync_yfinance_history(tickers=["BBAS3"])
    assert records == 3

    # Verifica o banco SQLite
    with repo._conn() as c:
        rows = c.execute("SELECT ticker, data_pregao, preco_fechamento, volume FROM price_history ORDER BY data_pregao").fetchall()
        assert len(rows) == 3
        assert rows[0]["ticker"] == "BBAS3"
        assert rows[0]["data_pregao"] == "2026-01-10"
        assert rows[0]["preco_fechamento"] == 17.10
        assert rows[2]["preco_fechamento"] == 17.50

def test_sync_yfinance_history_idempotent_upsert(tmp_path, monkeypatch):
    db_path = str(tmp_path / "yf_test.db")
    repo = BudgetRepository(db_path)

    initial_df = create_fake_df(
        dates=["2026-01-10"],
        closes=[17.10],
        volumes=[1000]
    )
    updated_df = create_fake_df(
        dates=["2026-01-10"],
        closes=[17.80],  # Preço atualizado / corrigido
        volumes=[2000]
    )

    mock_ticker = MagicMock()
    mock_ticker.history.side_effect = [initial_df, updated_df]

    mock_yf = MagicMock()
    mock_yf.Ticker.return_value = mock_ticker
    monkeypatch.setattr("yfinance.Ticker", mock_yf.Ticker)

    # 1ª execução
    r1 = repo.sync_yfinance_history(tickers=["BBAS3"])
    assert r1 == 1

    # 2ª execução com dados atualizados
    r2 = repo.sync_yfinance_history(tickers=["BBAS3"])
    assert r2 == 1

    # Confirma que não duplicou linhas e atualizou o valor
    with repo._conn() as c:
        rows = c.execute("SELECT * FROM price_history WHERE ticker = 'BBAS3'").fetchall()
        assert len(rows) == 1
        assert rows[0]["preco_fechamento"] == 17.80
        assert rows[0]["volume"] == 2000

def test_sync_yfinance_history_timeout_resilience(tmp_path, monkeypatch):
    db_path = str(tmp_path / "yf_test.db")
    repo = BudgetRepository(db_path)

    success_df = create_fake_df(
        dates=["2026-01-10"],
        closes=[6.30],
        volumes=[500000]
    )

    def fake_ticker_factory(symbol):
        ticker_mock = MagicMock()
        if "TIMEOUT" in symbol:
            # Simula timeout de requisição da API
            ticker_mock.history.side_effect = TimeoutError("Request timed out after 15s")
        else:
            ticker_mock.history.return_value = success_df
        return ticker_mock

    mock_yf = MagicMock()
    mock_yf.Ticker.side_effect = fake_ticker_factory
    monkeypatch.setattr("yfinance.Ticker", mock_yf.Ticker)

    # Tenta sincronizar um ticker com timeout e outro normal
    records = repo.sync_yfinance_history(tickers=["TIMEOUT3", "SAPR4"])
    # Deve continuar o fluxo e registrar SAPR4 com sucesso
    assert records == 1

    with repo._conn() as c:
        rows = c.execute("SELECT ticker, preco_fechamento FROM price_history").fetchall()
        assert len(rows) == 1
        assert rows[0]["ticker"] == "SAPR4"
        assert rows[0]["preco_fechamento"] == 6.30

def test_sync_yfinance_history_empty_or_none(tmp_path, monkeypatch):
    db_path = str(tmp_path / "yf_test.db")
    repo = BudgetRepository(db_path)

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()  # Vazio

    mock_yf = MagicMock()
    mock_yf.Ticker.return_value = mock_ticker
    monkeypatch.setattr("yfinance.Ticker", mock_yf.Ticker)

    records = repo.sync_yfinance_history(tickers=["EMPTY3"])
    assert records == 0

    with repo._conn() as c:
        rows = c.execute("SELECT count(*) as count FROM price_history").fetchone()
        assert rows["count"] == 0
