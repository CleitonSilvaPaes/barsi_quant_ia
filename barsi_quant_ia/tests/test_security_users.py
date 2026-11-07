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
    token = create_session_token(user_id=1, username="cleiton", role="admin", secret_key=secret, expires_in_sec=60)
    
    # Decodifica token válido
    data = decode_session_token(token, secret)
    assert data is not None
    assert data["user"] == "cleiton"
    assert data["role"] == "admin"
    
    # Assinatura corrompida deve ser rejeitada
    invalid_token = token[:-4] + "fake"
    assert decode_session_token(invalid_token, secret) is None

def test_user_repository_sqlite_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_users.db")
        repo = UserRepository(db_path)
        
        # 1. Cria admin
        ok1 = repo.create_user("cleiton", "senha123", role="admin")
        assert ok1 is True
        
        # 2. Cria leitor
        ok2 = repo.create_user("visitante", "leitor123", role="leitor")
        assert ok2 is True
        
        # 3. Usuário duplicado é rejeitado
        ok_dup = repo.create_user("cleiton", "outrasenha", role="admin")
        assert ok_dup is False
        
        # 4. Autenticação bem sucedida
        user_auth = repo.authenticate("cleiton", "senha123")
        assert user_auth is not None
        assert user_auth["username"] == "cleiton"
        assert user_auth["role"] == "admin"
        assert "password_hash" in user_auth
        
        # 5. Autenticação com senha errada falha
        assert repo.authenticate("cleiton", "senha_errada") is None
        assert repo.authenticate("usuario_inexistente", "qualquer") is None

