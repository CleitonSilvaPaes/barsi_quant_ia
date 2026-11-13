import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from barsi.api.http import app
from barsi.config import settings
from barsi.adapters.history.user_repository import UserRepository
from barsi.infra.security import (
    create_session_token,
    generate_csrf_token,
    generate_totp_secret,
    generate_totp_code,
    verify_totp_code,
)


@pytest.fixture
def test_db_repo(tmp_path):
    db_file = str(tmp_path / "test_auth_advanced.db")
    repo = UserRepository(db_file)
    return repo, db_file


def test_user_repository_email_and_google_methods(test_db_repo):
    repo, _ = test_db_repo
    # 1. Criação de usuário com email
    assert repo.create_user("alice", "senhaForte123", role="investidor", email="alice@exemplo.com")

    # 2. Busca por username, email e ambos
    u_user = repo.get_by_username("alice")
    assert u_user is not None
    assert u_user["email"] == "alice@exemplo.com"

    u_email = repo.get_by_email("ALICE@exemplo.com")
    assert u_email is not None
    assert u_email["username"] == "alice"

    u_by_both = repo.get_by_username_or_email("alice@exemplo.com")
    assert u_by_both is not None
    assert u_by_both["id"] == u_user["id"]

    # 3. Autenticação por email
    auth_email = repo.authenticate("alice@exemplo.com", "senhaForte123")
    assert auth_email is not None
    assert auth_email["username"] == "alice"

    # 4. Atualização de senha
    assert repo.update_password(u_user["id"], "novaSenhaSegura456")
    assert not repo.update_password(u_user["id"], "123")  # muito curta
    assert repo.authenticate("alice", "novaSenhaSegura456") is not None
    assert repo.authenticate("alice", "senhaForte123") is None

    # 5. Vinculação de conta Google
    assert repo.link_google_account(u_user["id"], "google-id-12345", "alice_google@gmail.com")
    u_google = repo.get_by_google_id("google-id-12345")
    assert u_google is not None
    assert u_google["username"] == "alice"

    # 6. Atualização de email
    assert repo.update_email(u_user["id"], "novo_email@exemplo.com")
    assert repo.get_by_email("novo_email@exemplo.com") is not None


def test_admin_change_password_and_email_api(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_admin_api.db")
    repo = UserRepository(db_file)
    repo.create_user("admin_user", "adminPass123", role="admin")
    repo.create_user("bob", "bobPass123", role="investidor")
    bob = repo.get_by_username("bob")

    monkeypatch.setattr("barsi.api.http.get_user_repo", lambda: repo)
    monkeypatch.setattr(settings, "csrf_enabled", True)

    client = TestClient(app)
    admin_token = create_session_token(1, "admin_user", "admin", settings.secret_key)
    admin_csrf = generate_csrf_token(admin_token, settings.secret_key)

    # Bob tenta trocar sua própria senha no endpoint de admin -> 403
    bob_token = create_session_token(bob["id"], "bob", "investidor", settings.secret_key)
    bob_csrf = generate_csrf_token(bob_token, settings.secret_key)
    resp_unauth = client.post(
        f"/api/admin/users/{bob['id']}/password",
        json={"new_password": "newBobPassword123"},
        cookies={"barsi_session": bob_token},
        headers={"X-CSRF-Token": bob_csrf}
    )
    assert resp_unauth.status_code == 403

    # Admin tenta senha muito curta -> 400
    resp_short = client.post(
        f"/api/admin/users/{bob['id']}/password",
        json={"new_password": "123"},
        cookies={"barsi_session": admin_token},
        headers={"X-CSRF-Token": admin_csrf}
    )
    assert resp_short.status_code == 400

    # Admin altera a senha de Bob com sucesso
    resp_ok = client.post(
        f"/api/admin/users/{bob['id']}/password",
        json={"new_password": "superSecretBob456"},
        cookies={"barsi_session": admin_token},
        headers={"X-CSRF-Token": admin_csrf}
    )
    assert resp_ok.status_code == 200
    assert resp_ok.json()["ok"] is True

    # Verifica se Bob autentica com a nova senha
    assert repo.authenticate("bob", "superSecretBob456") is not None
    assert repo.authenticate("bob", "bobPass123") is None

    # Admin altera o email de Bob
    resp_email = client.post(
        f"/api/admin/users/{bob['id']}/email",
        json={"email": "bob@dominio.com"},
        cookies={"barsi_session": admin_token},
        headers={"X-CSRF-Token": admin_csrf}
    )
    assert resp_email.status_code == 200
    assert repo.authenticate("bob@dominio.com", "superSecretBob456") is not None


def test_github_style_two_step_login_flow(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_2fa_flow.db")
    repo = UserRepository(db_file)
    repo.create_user("clara", "claraPass123", role="investidor", email="clara@empresa.com")
    clara = repo.get_by_username("clara")

    # Ativa 2FA para Clara
    secret, _ = repo.setup_totp(clara["id"])
    valid_code = generate_totp_code(secret)
    assert repo.confirm_and_enable_totp(clara["id"], valid_code)

    monkeypatch.setattr("barsi.api.http.get_user_repo", lambda: repo)
    monkeypatch.setattr(settings, "csrf_enabled", True)

    client = TestClient(app)

    # 1. Passo 1 estilo GitHub: Clara informa email e senha SEM marcar nada de 2FA
    resp_step1 = client.post(
        "/login",
        data={"username": "clara@empresa.com", "password": "claraPass123"},
        follow_redirects=False
    )
    # Deve ser redirecionada para /login/2fa com cookie barsi_2fa_pending
    assert resp_step1.status_code == 303
    assert resp_step1.headers["location"] == "/login/2fa"
    pending_cookie = resp_step1.cookies.get("barsi_2fa_pending")
    assert pending_cookie is not None

    # 2. Clara acessa /login/2fa
    resp_page_2fa = client.get("/login/2fa", cookies={"barsi_2fa_pending": pending_cookie})
    assert resp_page_2fa.status_code == 200
    assert "Two-factor authentication" in resp_page_2fa.text
    assert "XXXXXX" in resp_page_2fa.text

    # 3. Código 2FA inválido
    resp_invalid_code = client.post(
        "/login/2fa",
        data={"totp_code": "000000"},
        cookies={"barsi_2fa_pending": pending_cookie},
        follow_redirects=False
    )
    assert resp_invalid_code.status_code == 303
    assert "login/2fa?error=" in resp_invalid_code.headers["location"]

    # 4. Código 2FA válido
    code_now = generate_totp_code(secret)
    resp_valid_code = client.post(
        "/login/2fa",
        data={"totp_code": code_now},
        cookies={"barsi_2fa_pending": pending_cookie},
        follow_redirects=False
    )
    assert resp_valid_code.status_code == 303
    assert resp_valid_code.headers["location"] == "/"
    assert resp_valid_code.cookies.get("barsi_session") is not None


def test_google_oauth_flow(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_oauth.db")
    repo = UserRepository(db_file)
    repo.create_user("cleiton", "cleitonPass123", role="admin", email="cleiton03122000@gmail.com")

    monkeypatch.setattr("barsi.api.http.get_user_repo", lambda: repo)
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-client-secret")
    monkeypatch.setattr(settings, "google_redirect_uri", "http://localhost:8000/auth/google/callback")

    client = TestClient(app)

    # 1. Iniciar login com Google
    resp_init = client.get("/auth/google/login", follow_redirects=False)
    assert resp_init.status_code == 303
    assert "accounts.google.com" in resp_init.headers["location"]
    oauth_state = resp_init.cookies.get("barsi_oauth_state")
    assert oauth_state is not None

    # 2. Callback com estado forjado/inválido
    resp_bad_state = client.get("/auth/google/callback?code=fake&state=wrong", cookies={"barsi_oauth_state": oauth_state}, follow_redirects=False)
    assert resp_bad_state.status_code == 303
    assert "Estado+OAuth" in resp_bad_state.headers["location"]

    # 3. Callback simulado com sucesso via mock de httpx
    fake_token_resp = MagicMock()
    fake_token_resp.status_code = 200
    fake_token_resp.json.return_value = {"access_token": "google_token_xyz"}

    fake_userinfo_resp = MagicMock()
    fake_userinfo_resp.status_code = 200
    fake_userinfo_resp.json.return_value = {
        "sub": "google-sub-9999",
        "email": "cleiton03122000@gmail.com",
        "name": "Cleiton Paes"
    }

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = fake_token_resp
    mock_client_instance.get.return_value = fake_userinfo_resp
    mock_client_instance.__aenter__.return_value = mock_client_instance
    mock_client_instance.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        resp_callback = client.get(
            f"/auth/google/callback?code=valid_auth_code&state={oauth_state}",
            cookies={"barsi_oauth_state": oauth_state},
            follow_redirects=False
        )
        assert resp_callback.status_code == 303
        assert resp_callback.headers["location"] == "/"
        assert resp_callback.cookies.get("barsi_session") is not None

        # Confirma que a conta Google foi vinculada no banco
        cleiton = repo.get_by_username("cleiton")
        assert cleiton["google_id"] == "google-sub-9999"
