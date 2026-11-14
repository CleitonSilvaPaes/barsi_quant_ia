"""Testes para o Roadmap de Segurança, Recuperação e Auditoria do Barsi Quant IA."""
import pytest
import json
import time
from starlette.testclient import TestClient
from typer.testing import CliRunner

from barsi.config import settings
from barsi.adapters.history.user_repository import UserRepository
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.infra.security import (
    create_session_token,
    decode_session_token,
    generate_backup_codes,
    verify_backup_code,
)
from barsi.api.http import app, get_current_user
from barsi.__main__ import app as cli_app

runner = CliRunner()


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    from barsi.api import http
    http._login_attempts.clear()
    db_file = str(tmp_path / "test_sec.db")
    monkeypatch.setattr(settings, "sqlite_db_path", db_file)
    repo = UserRepository(db_file)
    budget_repo = BudgetRepository(db_file)
    yield db_file, repo, budget_repo
    http._login_attempts.clear()


def test_sqlite_wal_pragmas(clean_db):
    db_file, repo, budget_repo = clean_db
    with budget_repo._conn() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        # In-memory or file-backed SQLite in WAL mode
        assert journal_mode.lower() in ("wal", "memory")
        sync_mode = conn.execute("PRAGMA synchronous;").fetchone()[0]
        # 1 = NORMAL
        assert sync_mode in (1, "1", "NORMAL", "normal")


def test_backup_codes_lifecycle(clean_db):
    db_file, repo, _ = clean_db
    assert repo.create_user("alice", "senha123", role="investidor")
    user = repo.get_by_username("alice")
    assert user is not None

    # Gera 8 backup codes
    codes = repo.generate_backup_codes(user["id"], count=8)
    assert len(codes) == 8
    for c in codes:
        assert len(c) == 14  # xxxx-xxxx-xxxx
        assert c.count("-") == 2

    assert repo.get_backup_codes_count(user["id"]) == 8

    # Consome o primeiro código
    first_code = codes[0]
    assert repo.verify_and_consume_backup_code(user["id"], first_code) is True
    assert repo.get_backup_codes_count(user["id"]) == 7

    # Tenta reutilizar o código consumido (deve falhar)
    assert repo.verify_and_consume_backup_code(user["id"], first_code) is False
    assert repo.get_backup_codes_count(user["id"]) == 7

    # Código inválido
    assert repo.verify_and_consume_backup_code(user["id"], "0000-0000-0000") is False


def test_session_revocation(clean_db):
    db_file, repo, _ = clean_db
    assert repo.create_user("bob", "senha123", role="admin")
    user = repo.get_by_username("bob")

    token = create_session_token(user["id"], user["username"], user["role"], settings.secret_key, jti="test-jti-123")
    decoded = decode_session_token(token, settings.secret_key)
    assert decoded is not None
    assert decoded["jti"] == "test-jti-123"

    assert repo.is_session_revoked("test-jti-123") is False

    # Revoga a sessão
    repo.revoke_session("test-jti-123", user_id=user["id"], expires_at=decoded["exp"])
    assert repo.is_session_revoked("test-jti-123") is True


def test_cli_user_pass_reset(clean_db):
    db_file, repo, _ = clean_db
    repo.create_user("carol", "senhaAntiga123", role="investidor")

    result = runner.invoke(
        cli_app,
        ["user-pass-reset", "carol", "--password", "novaSenhaSegura456", "--force"]
    )
    assert result.exit_code == 0
    assert "atualizada com sucesso" in result.output

    # Autenticação deve funcionar com nova senha
    user = repo.authenticate("carol", "novaSenhaSegura456")
    assert user is not None

    # Audit log deve conter a ação
    logs = repo.list_audit_logs()
    assert any(l["action"] == "cli_pass_reset" and l["target"] == "carol" for l in logs)


def test_cli_user_disable_2fa(clean_db):
    db_file, repo, _ = clean_db
    repo.create_user("david", "senha123", role="admin")
    user = repo.get_by_username("david")

    secret, uri = repo.setup_totp(user["id"])
    from barsi.infra.security import generate_totp_code
    totp_code = generate_totp_code(secret)
    assert repo.confirm_and_enable_totp(user["id"], totp_code) is True
    repo.generate_backup_codes(user["id"])
    assert repo.is_totp_enabled(user["id"]) is True

    result = runner.invoke(
        cli_app,
        ["user-disable-2fa", "david", "--reason", "perdi_celular", "--force"]
    )
    assert result.exit_code == 0
    assert "2FA e códigos de backup desativados" in result.output

    user_after = repo.get_by_username("david")
    assert user_after["totp_enabled"] == 0
    assert user_after["totp_secret"] is None
    assert user_after["backup_codes_hash"] is None

    logs = repo.list_audit_logs()
    assert any(l["action"] == "cli_disable_2fa" and l["target"] == "david" for l in logs)


def test_admin_cannot_reset_own_password_without_current_password(clean_db):
    db_file, repo, _ = clean_db
    repo.create_user("admin_chief", "masterPassword123", role="admin")
    admin = repo.get_by_username("admin_chief")

    client = TestClient(app)
    token = create_session_token(admin["id"], admin["username"], "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    # Tenta alterar a própria senha via rota administrativa (deve ser bloqueado)
    res = client.post(
        f"/api/admin/users/{admin['id']}/password",
        json={"new_password": "newMasterPassword999"}
    )
    assert res.status_code == 400
    assert "não podem alterar a própria senha" in res.json()["detail"]


def test_2fa_enable_returns_backup_codes(clean_db):
    db_file, repo, _ = clean_db
    repo.create_user("elena", "senha123", role="admin")
    user = repo.get_by_username("elena")

    client = TestClient(app)
    token = create_session_token(user["id"], user["username"], "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    # Setup 2FA
    res_setup = client.post("/api/auth/2fa/setup")
    assert res_setup.status_code == 200
    secret = res_setup.json()["secret"]

    from barsi.infra.security import generate_totp_code
    valid_code = generate_totp_code(secret)

    # Enable 2FA -> deve retornar backup_codes
    res_enable = client.post("/api/auth/2fa/enable", json={"code": valid_code})
    assert res_enable.status_code == 200
    data = res_enable.json()
    assert data["ok"] is True
    assert "backup_codes" in data
    assert len(data["backup_codes"]) == 8


def test_login_with_backup_code(clean_db):
    db_file, repo, _ = clean_db
    repo.create_user("frank", "senhaSegura123", role="investidor")
    user = repo.get_by_username("frank")

    secret, _ = repo.setup_totp(user["id"])
    from barsi.infra.security import generate_totp_code
    repo.confirm_and_enable_totp(user["id"], generate_totp_code(secret))
    backup_codes = repo.generate_backup_codes(user["id"])

    client = TestClient(app)

    # Passo 1: Login inicial -> redireciona para /login/2fa com cookie barsi_2fa_pending
    res_step1 = client.post(
        "/login",
        data={"username": "frank", "password": "senhaSegura123"},
        follow_redirects=False
    )
    assert res_step1.status_code == 303
    assert res_step1.headers["location"] == "/login/2fa"
    pending_cookie = res_step1.cookies.get("barsi_2fa_pending")
    assert pending_cookie is not None

    # Passo 2: Fornece o código de recuperação (backup code)
    client.cookies.set("barsi_2fa_pending", pending_cookie)
    res_step2 = client.post(
        "/login/2fa",
        data={"totp_code": backup_codes[0]},
        follow_redirects=False
    )
    assert res_step2.status_code == 303
    assert res_step2.headers["location"] == "/"
    assert client.cookies.get("barsi_session") is not None

    # O código consumido não deve funcionar novamente
    assert repo.get_backup_codes_count(user["id"]) == 7


def test_logout_revokes_jti(clean_db):
    db_file, repo, _ = clean_db
    repo.create_user("grace", "senha123", role="admin")
    user = repo.get_by_username("grace")

    client = TestClient(app)
    token = create_session_token(user["id"], user["username"], "admin", settings.secret_key, jti="jti-grace-777")
    client.cookies.set("barsi_session", token)

    # Faz logout
    res_logout = client.get("/logout", follow_redirects=False)
    assert res_logout.status_code == 303
    assert repo.is_session_revoked("jti-grace-777") is True


def test_security_headers_and_cache_control(clean_db):
    clean_db
    client = TestClient(app)

    # Rota raiz HTML
    res_root = client.get("/login")
    assert res_root.headers.get("X-Content-Type-Options") == "nosniff"
    assert res_root.headers.get("Cross-Origin-Opener-Policy") == "same-origin"
    assert res_root.headers.get("Cross-Origin-Resource-Policy") == "same-origin"

    # Rota estática
    res_static = client.get("/static/manifest.json")
    if res_static.status_code == 200:
        assert "max-age=86400" in res_static.headers.get("Cache-Control", "")


def test_admin_audit_logs_endpoint(clean_db):
    db_file, repo, _ = clean_db
    repo.create_user("adm", "senha123", role="admin")
    adm = repo.get_by_username("adm")
    repo.log_audit("test_audit_event", actor_id=adm["id"], actor_username="adm", reason="unit_test")

    client = TestClient(app)
    token = create_session_token(adm["id"], "adm", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    res = client.get("/api/admin/audit-logs")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert any(l["action"] == "test_audit_event" for l in data["logs"])
