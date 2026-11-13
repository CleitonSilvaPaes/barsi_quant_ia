"""Configuração global de fixtures do pytest para o ecossistema Barsi Quant IA.

Garante isolamento absoluto de banco de dados: NENHUM teste toca no banco
de dados SQLite de produção (memory/barsi_memory.db).
"""
import pytest
from barsi.config import settings
import barsi.api.http as http_module


@pytest.fixture(autouse=True)
def isolate_database(tmp_path, monkeypatch):
    """Garante que NENHUM teste toque no banco de produção.

    Cria um banco temporário isolado por teste e redireciona sqlite_db_path
    para tmp_path, resetando caches em memória.
    """
    test_db = str(tmp_path / "test_isolated.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setenv("BARSI_SQLITE_PATH", test_db)
    monkeypatch.setattr(http_module, "_user_repo_cache", None)

    yield test_db

    monkeypatch.setattr(http_module, "_user_repo_cache", None)

