"""Testes unitários para segurança criptográfica, RBAC e repositório de usuários."""
import tempfile
import os
from barsi.infra.security import hash_password, verify_password, create_session_token, decode_session_token
from barsi.adapters.history.user_repository import UserRepository

def test_password_hashing_and_verification():
    plain = "MinhaSenhaForte@2026"
    hashed = hash_password(plain)
    
    # 1. Deve ser diferente do texto puro
    assert plain != hashed
    assert "$" in hashed
    
    # 2. Verificação válida
    assert verify_password(plain, hashed) is True
    
    # 3. Senha incorreta deve falhar
    assert verify_password("SenhaErrada", hashed) is False

def test_session_token_signature_and_expiration():
    secret = "chave_secreta_de_teste_123456"
    token = create_session_token(user_id=1, username="admin_user", role="admin", secret_key=secret, expires_in_sec=60)
    
    # Decodifica token válido
    data = decode_session_token(token, secret)
    assert data is not None
    assert data["user"] == "admin_user"
    assert data["role"] == "admin"
    
    # Assinatura corrompida deve ser rejeitada
    invalid_token = token[:-4] + "fake"
    assert decode_session_token(invalid_token, secret) is None

def test_user_repository_sqlite_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_users.db")
        repo = UserRepository(db_path)
        
        # 1. Cria admin
        ok1 = repo.create_user("admin_user", "senha123", role="admin")
        assert ok1 is True
        
        # 2. Cria leitor
        ok2 = repo.create_user("visitante", "leitor123", role="leitor")
        assert ok2 is True
        
        # 3. Usuário duplicado é rejeitado
        ok_dup = repo.create_user("admin_user", "outrasenha", role="admin")
        assert ok_dup is False
        
        # 4. Autenticação bem sucedida
        user_auth = repo.authenticate("admin_user", "senha123")
        assert user_auth is not None
        assert user_auth["username"] == "admin_user"
        assert user_auth["role"] == "admin"
        assert "password_hash" in user_auth
        
        # 5. Autenticação com senha errada falha
        assert repo.authenticate("admin_user", "senha_errada") is None
        assert repo.authenticate("usuario_inexistente", "qualquer") is None


def test_login_rate_limiting(monkeypatch, tmp_path):
    """Valida que mais de 5 tentativas de login falhas no minuto bloqueiam tentativas subsequentes."""
    from fastapi.testclient import TestClient
    from barsi.api.http import app, _login_attempts
    import barsi.api.http as http_module
    from barsi.config import settings

    test_db = str(tmp_path / "test_rl.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    http_module._user_repo_cache = None
    _login_attempts.clear()

    client = TestClient(app)

    # 5 tentativas com senha errada
    for i in range(5):
        resp = client.post("/login", data={"username": "user", "password": "wrong_password"}, follow_redirects=False)
        assert resp.status_code == 303
        assert "error=Usu%C3%A1rio" in resp.headers["location"] or "error=Usuário" in resp.headers["location"]

    # 6ª tentativa deve ser bloqueada por rate limit
    blocked_resp = client.post("/login", data={"username": "user", "password": "wrong_password"}, follow_redirects=False)
    assert blocked_resp.status_code == 303
    assert "Muitas+tentativas" in blocked_resp.headers["location"]


