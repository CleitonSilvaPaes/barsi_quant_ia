"""Testes de segurança para proteção contra ataques Cross-Site Request Forgery (CSRF)."""
from fastapi.testclient import TestClient
from barsi.api.http import app
from barsi.config import settings
from barsi.infra.security import (
    create_session_token,
    generate_csrf_token,
    verify_csrf_token,
)
from barsi.adapters.factory import AdapterFactory
from barsi.domain.models import Position


class MockSheetsAdapter:
    def __init__(self):
        self.positions = [
            Position(ticker="BBAS3", row_index=10, preco_atual=17.13, div_proj=2.30, qtd_antes=37)
        ]

    async def load_positions(self):
        return self.positions

    async def update_positions(self, positions, invalidate=True):
        self.positions = positions


def test_csrf_token_crypto_generation_and_verification():
    """Valida a geração de tokens CSRF vinculados à sessão e anônimos via HMAC."""
    session_a = "session_user_a_token_123"
    session_b = "session_user_b_token_456"
    secret = "test_super_secret_key"

    token_a = generate_csrf_token(session_a, secret)
    token_b = generate_csrf_token(session_b, secret)

    # Tokens são diferentes para sessões distintas
    assert token_a != token_b
    assert len(token_a) == 64  # SHA-256 hex digest

    # Validação com token e sessão corretos
    assert verify_csrf_token(token_a, session_a, secret) is True
    assert verify_csrf_token(token_b, session_b, secret) is True

    # Rejeição com sessão invertida (cross-session forgery)
    assert verify_csrf_token(token_a, session_b, secret) is False
    assert verify_csrf_token(token_b, session_a, secret) is False

    # Rejeição com token adulterado ou vazio
    assert verify_csrf_token("invalid_token_xyz", session_a, secret) is False
    assert verify_csrf_token("", session_a, secret) is False
    assert verify_csrf_token(None, session_a, secret) is False

    # Token anônimo (pré-login)
    anon_token = generate_csrf_token(None, secret)
    assert "." in anon_token
    assert verify_csrf_token(anon_token, None, secret) is True
    assert verify_csrf_token("tampered.fake_signature", None, secret) is False


def test_csrf_cookie_and_meta_tag_on_page_render():
    """Garante que a renderização de páginas inclui a meta tag e o cookie barsi_csrf."""
    client = TestClient(app)
    token = create_session_token(user_id=1, username="admin_test", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)

    res = client.get("/carteira")
    assert res.status_code == 200
    assert "barsi_csrf" in res.cookies
    assert '<meta name="csrf-token" content="' in res.text

    # Cookie deve bater com o valor na meta tag
    csrf_cookie = res.cookies.get("barsi_csrf")
    assert f'content="{csrf_cookie}"' in res.text


def test_csrf_login_page_renders_token():
    """Garante que a página de login injeta a meta tag e o cookie CSRF."""
    client = TestClient(app)
    res = client.get("/login")
    assert res.status_code == 200
    assert "barsi_csrf" in res.cookies
    assert '<meta name="csrf-token" content="' in res.text
    assert '<input type="hidden" name="csrf_token"' in res.text


def test_csrf_blocks_authenticated_post_when_token_is_missing(monkeypatch):
    """POST com cookie de sessão sem cabeçalho X-CSRF-Token DEVE ser bloqueado com 403."""
    mock_sheets = MockSheetsAdapter()
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda cfg: mock_sheets)

    client = TestClient(app)
    token = create_session_token(user_id=1, username="admin_test", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)

    payload = {"items": [{"ticker": "BBAS3", "row_index": 10, "div_proj": 2.30, "qtd_antes": 37}]}
    # O header X-Skip-Auto-CSRF instrui a fixture de testes a NÃO auto-injetar o token
    res = client.post("/api/carteira", json=payload, headers={"X-Skip-Auto-CSRF": "1"})

    assert res.status_code == 403
    data = res.json()
    assert "CSRF" in data.get("detail", "")


def test_csrf_blocks_authenticated_post_when_token_is_invalid(monkeypatch):
    """POST com cookie de sessão e cabeçalho X-CSRF-Token forjado DEVE ser bloqueado com 403."""
    mock_sheets = MockSheetsAdapter()
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda cfg: mock_sheets)

    client = TestClient(app)
    token = create_session_token(user_id=1, username="admin_test", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)

    payload = {"items": [{"ticker": "BBAS3", "row_index": 10, "div_proj": 2.30, "qtd_antes": 37}]}
    res = client.post("/api/carteira", json=payload, headers={"X-CSRF-Token": "evil_forged_csrf_token_12345"})

    assert res.status_code == 403
    data = res.json()
    assert "CSRF" in data.get("detail", "")


def test_csrf_allows_authenticated_post_with_valid_token(monkeypatch):
    """POST com cookie de sessão e X-CSRF-Token válido DEVE ter sucesso."""
    mock_sheets = MockSheetsAdapter()
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda cfg: mock_sheets)

    client = TestClient(app)
    token = create_session_token(user_id=1, username="admin_test", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)

    valid_csrf = generate_csrf_token(token, settings.secret_key)
    payload = {"items": [{"ticker": "BBAS3", "row_index": 10, "div_proj": 2.30, "qtd_antes": 37}]}
    res = client.post("/api/carteira", json=payload, headers={"X-CSRF-Token": valid_csrf})

    assert res.status_code == 200


def test_csrf_exempt_routes_login_allowed():
    """Garante que a rota isenta /login pode receber POST sem token de sessão."""
    client = TestClient(app)
    res = client.post("/login", data={"username": "wrong", "password": "bad"}, follow_redirects=False)
    assert res.status_code in (302, 303)
    assert "/login?error=" in res.headers.get("location", "")


def test_csrf_can_be_disabled_via_settings(monkeypatch):
    """Garante que se settings.csrf_enabled for False, POST sem token passa."""
    monkeypatch.setattr(settings, "csrf_enabled", False)
    mock_sheets = MockSheetsAdapter()
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda cfg: mock_sheets)

    client = TestClient(app)
    token = create_session_token(user_id=1, username="admin_test", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)

    payload = {"items": [{"ticker": "BBAS3", "row_index": 10, "div_proj": 2.30, "qtd_antes": 37}]}
    res = client.post("/api/carteira", json=payload, headers={"X-Skip-Auto-CSRF": "1"})

    assert res.status_code == 200
