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
        session_token = cookies.get("barsi_session") if isinstance(cookies, dict) else None
        if not session_token:
            try:
                session_token = self.cookies.get("barsi_session")
            except Exception:
                for c in reversed(list(self.cookies.jar)):
                    if c.name == "barsi_session":
                        session_token = c.value
                        break
        if session_token:
            csrf_token = generate_csrf_token(session_token, settings.secret_key)
            if isinstance(headers, dict):
                headers["X-CSRF-Token"] = csrf_token

    return _orig_request(self, method, url, *args, **kwargs)

StarletteTestClient.request = _patched_request


_client_instances = []
_orig_client_init = StarletteTestClient.__init__

def _patched_client_init(self, *args, **kwargs):
    _orig_client_init(self, *args, **kwargs)
    _client_instances.append(self)

StarletteTestClient.__init__ = _patched_client_init


@pytest.fixture(autouse=True)
def isolate_database(tmp_path, monkeypatch):
    """Garante que NENHUM teste toque no banco de produção.

    Cria um banco temporário isolado por teste e redireciona sqlite_db_path
    para tmp_path, resetando caches em memória e limpando cookies de clientes de teste.
    """
    test_db = str(tmp_path / "test_isolated.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setenv("BARSI_SQLITE_PATH", test_db)
    monkeypatch.setattr(http_module, "_user_repo_cache", None)
    http_module._login_attempts.clear()
    for c in _client_instances:
        try:
            c.cookies.clear()
        except Exception:
            pass

    yield test_db

    monkeypatch.setattr(http_module, "_user_repo_cache", None)
    http_module._login_attempts.clear()
    for c in _client_instances:
        try:
            c.cookies.clear()
        except Exception:
            pass

