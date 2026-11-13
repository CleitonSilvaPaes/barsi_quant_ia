"""Configuração global de fixtures do pytest para o ecossistema Barsi Quant IA.

Garante isolamento absoluto de banco de dados: NENHUM teste toca no banco
de dados SQLite de produção (memory/barsi_memory.db).
"""
import pytest
from starlette.testclient import TestClient as StarletteTestClient
from barsi.config import settings
from barsi.infra.security import generate_csrf_token
import barsi.api.http as http_module

# Auto-injeta cabeçalho X-CSRF-Token em requisições de testes autenticadas via cookies,
# permitindo testar rejeição explícita passando X-Skip-Auto-CSRF ou tokens inválidos.
_orig_request = StarletteTestClient.request

def _patched_request(self, method, url, *args, **kwargs):
    headers = kwargs.get("headers")
    if headers is None:
        headers = {}
        kwargs["headers"] = headers

    skip_auto = False
    if isinstance(headers, dict) and "X-Skip-Auto-CSRF" in headers:
        headers.pop("X-Skip-Auto-CSRF")
        skip_auto = True

    has_explicit_csrf = isinstance(headers, dict) and any(
        k.lower() in ("x-csrf-token", "x-csrftoken") for k in headers.keys()
    )

    if not skip_auto and not has_explicit_csrf:
        cookies = kwargs.get("cookies") or {}
        session_token = cookies.get("barsi_session") or self.cookies.get("barsi_session")
        if session_token:
            csrf_token = generate_csrf_token(session_token, settings.secret_key)
            if isinstance(headers, dict):
                headers["X-CSRF-Token"] = csrf_token

    return _orig_request(self, method, url, *args, **kwargs)

StarletteTestClient.request = _patched_request


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

