"""Testes automatizados para CSRF por Sessão com persistência em SQLite (session_csrf) e rotação."""
import time
from starlette.testclient import TestClient

from barsi.config import settings
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.user_repository import UserRepository
from barsi.domain.models import Position
from barsi.infra.security import decode_session_token
from barsi.api.http import app


class MockSheetsAdapter:
    def __init__(self):
        self.positions = [
            Position(ticker="BBAS3", row_index=10, preco_atual=25.0, div_proj=2.5, qtd_antes=100)
        ]

    async def load_positions(self):
        return self.positions

    async def update_positions(self, positions, invalidate=True):
        self.positions = positions


def test_session_csrf_repository_crud_and_rotation(tmp_path):
    """Valida métodos CRUD, geração token_urlsafe(32) e rotação atômica em session_csrf."""
    db_file = str(tmp_path / "test_session_csrf.db")
    repo = UserRepository(db_file)

    session_id = "test_jti_session_123"
    assert repo.get_session_csrf(session_id) is None

    # 1. Rotação inicial gera token criptográfico
    csrf_token = repo.rotate_session_csrf(session_id, user_id=1)
    assert csrf_token is not None
    assert len(csrf_token) >= 42  # token_urlsafe(32) gera ~43 caracteres
    assert repo.get_session_csrf(session_id) == csrf_token

    # 2. Rotação subsequente gera um novo token diferente
    new_token = repo.rotate_session_csrf(session_id, user_id=1)
    assert new_token != csrf_token
    assert repo.get_session_csrf(session_id) == new_token

    # 3. Deleção ao deslogar
    repo.delete_session_csrf(session_id)
    assert repo.get_session_csrf(session_id) is None


def test_login_persists_session_csrf_and_sets_cookie(tmp_path, monkeypatch):
    """Garante que o login web vincula o token CSRF à sessão no banco e injeta cookie."""
    db_file = str(tmp_path / "test_auth_csrf.db")
    monkeypatch.setattr(settings, "sqlite_db_path", db_file)
    monkeypatch.setattr(settings, "telegram_alerts_enabled", False)
    repo = UserRepository(db_file)
    repo.create_user("investidor_teste", "senhaForte123", role="investidor")

    client = TestClient(app)
    res = client.post("/login", data={"username": "investidor_teste", "password": "senhaForte123"}, follow_redirects=False)

    assert res.status_code == 303
    session_token = res.cookies.get("barsi_session")
    csrf_cookie = res.cookies.get("barsi_csrf")
    assert session_token is not None
    assert csrf_cookie is not None

    # Decodifica JTI e verifica na tabela session_csrf
    data = decode_session_token(session_token, settings.secret_key)
    assert data is not None
    jti = data["jti"]
    stored_csrf = repo.get_session_csrf(jti)
    assert stored_csrf == csrf_cookie


def test_mutation_accepts_session_csrf_and_rejects_rotated_old_token(tmp_path, monkeypatch):
    """Mutação POST aceita o session_csrf ativo e bloqueia imediatamente com 403 após rotação."""
    db_file = str(tmp_path / "test_csrf_mutate.db")
    monkeypatch.setattr(settings, "sqlite_db_path", db_file)
    monkeypatch.setattr(settings, "telegram_alerts_enabled", False)
    mock_sheets = MockSheetsAdapter()
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda cfg: mock_sheets)

    repo = UserRepository(db_file)
    repo.create_user("admin_mutate", "senhaAdmin123", role="admin")

    client = TestClient(app)
    # Login para obter sessão e token CSRF registrado
    res_login = client.post("/login", data={"username": "admin_mutate", "password": "senhaAdmin123"}, follow_redirects=False)
    session_token = res_login.cookies.get("barsi_session")
    csrf_cookie = res_login.cookies.get("barsi_csrf")

    client.cookies.set("barsi_session", session_token)
    client.cookies.set("barsi_csrf", csrf_cookie)

    payload = {"items": [{"ticker": "BBAS3", "row_index": 10, "div_proj": 2.50, "qtd_antes": 100}]}

    # 1. Mutação com token ativo: Sucesso (200)
    res_ok = client.post("/api/carteira", json=payload, headers={"X-CSRF-Token": csrf_cookie})
    assert res_ok.status_code == 200

    # 2. Rotação do token no servidor (simula refresh/rotação da sessão)
    data = decode_session_token(session_token, settings.secret_key)
    jti = data["jti"]
    new_rotated_csrf = repo.rotate_session_csrf(jti, user_id=data["uid"])
    assert new_rotated_csrf != csrf_cookie

    # 3. Requisição com token antigo expirado/rotacionado: Bloqueada (403)
    res_blocked = client.post("/api/carteira", json=payload, headers={"X-CSRF-Token": csrf_cookie})
    assert res_blocked.status_code == 403
    assert "CSRF" in res_blocked.json().get("detail", "")

    # 4. Requisição com novo token rotacionado: Sucesso (200)
    res_new_ok = client.post("/api/carteira", json=payload, headers={"X-CSRF-Token": new_rotated_csrf})
    assert res_new_ok.status_code == 200


def test_logout_invalidates_session_csrf(tmp_path, monkeypatch):
    """Logout remove o token da tabela session_csrf."""
    db_file = str(tmp_path / "test_csrf_logout.db")
    monkeypatch.setattr(settings, "sqlite_db_path", db_file)
    monkeypatch.setattr(settings, "telegram_alerts_enabled", False)

    repo = UserRepository(db_file)
    repo.create_user("user_logout", "senha123456", role="leitor")

    client = TestClient(app)
    res_login = client.post("/login", data={"username": "user_logout", "password": "senha123456"}, follow_redirects=False)
    session_token = res_login.cookies.get("barsi_session")

    data = decode_session_token(session_token, settings.secret_key)
    jti = data["jti"]
    assert repo.get_session_csrf(jti) is not None

    client.cookies.set("barsi_session", session_token)
    res_logout = client.get("/logout", follow_redirects=False)
    assert res_logout.status_code == 303
    # Token foi removido da tabela session_csrf
    assert repo.get_session_csrf(jti) is None
