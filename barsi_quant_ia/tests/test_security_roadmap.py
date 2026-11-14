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
    csp = res_root.headers.get("Content-Security-Policy", "")
    assert "connect-src 'self';" in csp
    assert "api.qrserver.com" not in csp
    assert "img-src 'self' data: https:;" in csp

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


def test_admin_recovery_after_losing_phone_and_forgetting_password(clean_db):
    """Cenário completo de emergência: perdeu celular + esqueceu senha -> CLI recupera ambos."""
    db_file, repo, _ = clean_db
    repo.create_user("vittima", "senhaAntiga123", role="admin")
    user = repo.get_by_username("vittima")
    secret, _ = repo.setup_totp(user["id"])
    from barsi.infra.security import generate_totp_code
    repo.confirm_and_enable_totp(user["id"], generate_totp_code(secret))
    repo.generate_backup_codes(user["id"])

    # Admin não consegue logar (senha incorreta)
    client = TestClient(app)
    r1 = client.post("/login", data={"username": "vittima", "password": "errada"}, follow_redirects=False)
    assert r1.status_code == 303

    # 1. CLI reseta senha
    res_cli = runner.invoke(cli_app, ["user-pass-reset", "vittima", "--password", "novaSenhaSegura456", "--force"])
    assert res_cli.exit_code == 0

    # 2. Login com nova senha -> redireciona para /login/2fa (pois 2FA continua ativo)
    r2 = client.post("/login", data={"username": "vittima", "password": "novaSenhaSegura456"}, follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/login/2fa"

    # 3. CLI desativa 2FA de emergência
    res_cli2 = runner.invoke(cli_app, ["user-disable-2fa", "vittima", "--force"])
    assert res_cli2.exit_code == 0

    # 4. Login com nova senha agora entra direto
    r3 = client.post("/login", data={"username": "vittima", "password": "novaSenhaSegura456"}, follow_redirects=False)
    assert r3.status_code == 303
    assert client.cookies.get("barsi_session") is not None

    # 5. Ações registradas em audit_log
    logs = repo.list_audit_logs()
    assert any(l["action"] == "cli_pass_reset" and l["target"] == "vittima" for l in logs)
    assert any(l["action"] == "cli_disable_2fa" and l["target"] == "vittima" for l in logs)


def test_cache_invalidation_on_delete(clean_db):
    """Excluir uma posição da carteira invalida o cache de posições imediatamente."""
    db_file, repo, budget_repo = clean_db
    from barsi.domain.models import Position
    from barsi.api.http import get_canonical_positions

    budget_repo.save_positions([
        Position(ticker="BBAS3", row_index=10, preco_atual=10.0),
        Position(ticker="SAPR4", row_index=11, preco_atual=5.0)
    ])

    client = TestClient(app)
    admin_token = create_session_token(1, "admin", "admin", settings.secret_key)
    client.cookies.set("barsi_session", admin_token)

    # Exclui BBAS3 via endpoint
    res_del = client.delete("/api/carteira/BBAS3")
    assert res_del.status_code == 200

    # Próxima consulta já não contém BBAS3
    res_carteira = client.get("/api/carteira")
    assert res_carteira.status_code == 200
    tickers = [item["ticker"] for item in res_carteira.json()]
    assert "BBAS3" not in tickers
    assert "SAPR4" in tickers


def test_maintenance_cleanup_cli(clean_db):
    """Comando CLI maintenance-cleanup remove registros expirados de sessões e tentativas de login."""
    db_file, repo, _ = clean_db
    # Insere dados expirados
    with repo._connection() as conn:
        conn.execute(
            "INSERT INTO session_revocation (jti, user_id, revoked_at, expires_at) VALUES (?, ?, ?, ?)",
            ("expired-jti-001", 1, time.time() - 200, time.time() - 100)
        )
        conn.execute(
            "INSERT INTO login_attempts (ip, attempted_at) VALUES (?, ?)",
            ("1.2.3.4", time.time() - 90000)
        )
        conn.commit()

    result = runner.invoke(cli_app, ["maintenance-cleanup"])
    assert result.exit_code == 0
    assert "Manutenção concluída" in result.output

    with repo._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_revocation WHERE jti = 'expired-jti-001'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM login_attempts WHERE ip = '1.2.3.4'").fetchone()[0] == 0


def test_user_change_password_endpoint(clean_db):
    """Usuário autenticado altera sua própria senha comprovando a senha atual."""
    db_file, repo, _ = clean_db
    repo.create_user("helena", "senhaOriginal123", role="investidor")
    user = repo.get_by_username("helena")

    client = TestClient(app)
    token = create_session_token(user["id"], user["username"], user["role"], settings.secret_key)
    client.cookies.set("barsi_session", token)

    # Senha atual incorreta -> 400
    res_bad = client.post(
        "/api/user/change-password",
        json={"current_password": "senhaErrada", "new_password": "novaSenhaForte456"}
    )
    assert res_bad.status_code == 400
    assert "Senha atual incorreta" in res_bad.json()["detail"]

    # Senha atual correta -> 200
    res_ok = client.post(
        "/api/user/change-password",
        json={"current_password": "senhaOriginal123", "new_password": "novaSenhaForte456"}
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["ok"] is True

    # Verifica se a nova senha funciona
    assert repo.authenticate("helena", "novaSenhaForte456") is not None


def test_admin_login_redirects_to_2fa_setup(clean_db):
    """Admin sem 2FA ativo é redirecionado para /settings#2fa para configuração mandatória."""
    db_file, repo, _ = clean_db
    repo.create_user("master_admin", "adminPassword123", role="admin")

    client = TestClient(app)
    res = client.post(
        "/login",
        data={"username": "master_admin", "password": "adminPassword123"},
        follow_redirects=False
    )
    assert res.status_code == 303
    assert "/settings" in res.headers["location"]
    assert "2fa" in res.headers["location"].lower()


def test_disable_totp_requires_proof_by_default(clean_db):
    """disable_totp requer totp_code válido ou bypass_verification=True por padrão de segurança."""
    _, repo, _ = clean_db
    repo.create_user("teste_totp_proof", "senha123", role="admin")
    user = repo.get_by_username("teste_totp_proof")
    secret, _ = repo.setup_totp(user["id"])
    from barsi.infra.security import generate_totp_code
    valid_code = generate_totp_code(secret)
    repo.confirm_and_enable_totp(user["id"], valid_code)

    # Sem código e sem bypass -> deve levantar ValueError
    with pytest.raises(ValueError, match="totp_code válido"):
        repo.disable_totp(user["id"])

    # Código errado -> deve levantar ValueError
    with pytest.raises(ValueError):
        repo.disable_totp(user["id"], totp_code="000000")

    # Código correto -> desativa
    assert repo.disable_totp(user["id"], totp_code=valid_code) is True
    assert repo.is_totp_enabled(user["id"]) is False


def test_session_version_revocation_on_password_change(clean_db):
    """Troca de senha preserva a sessão atual e invalida tokens de sessão antigos via session_version."""
    _, repo, _ = clean_db
    repo.create_user("roberta", "senhaAntiga123", role="investidor")
    user = repo.get_by_username("roberta")

    # Cria token antigo com sv=1
    token_old = create_session_token(
        user["id"], user["username"], user["role"], settings.secret_key, session_version=user.get("session_version", 1)
    )

    client = TestClient(app)
    client.cookies.set("barsi_session", token_old)

    # Endpoint change-password altera a senha e eleva session_version
    res = client.post(
        "/api/user/change-password",
        json={"current_password": "senhaAntiga123", "new_password": "novaSenhaSuperSegura456"}
    )
    assert res.status_code == 200

    # Sessão atual continua logada e operacional (UX padrão GitHub/Devise)
    res_after = client.get("/api/home/summary")
    assert res_after.status_code == 200

    # Token antigo usado por outro cliente deve ser rejeitado agora
    client_stolen = TestClient(app)
    client_stolen.cookies.set("barsi_session", token_old)
    res_carteira = client_stolen.get("/api/home/summary")
    assert res_carteira.status_code == 401


def test_token_invalidated_after_user_deletion(clean_db):
    """Deletar usuário invalida imediatamente todos os seus tokens ativos (Bug #1 Hotfix)."""
    _, repo, _ = clean_db
    repo.create_user("victim", "senha123", role="investidor")
    user = repo.get_by_username("victim")

    token = create_session_token(
        user["id"], user["username"], user["role"],
        settings.secret_key, session_version=user.get("session_version", 1)
    )

    client = TestClient(app)
    client.cookies.set("barsi_session", token)
    # Token funciona perfeitamente antes da exclusão
    assert client.get("/api/home/summary").status_code == 200

    # Admin deleta o usuário diretamente no repositório
    with repo._connection() as conn:
        conn.execute("DELETE FROM usuarios WHERE id = ?", (user["id"],))
        conn.commit()

    # Token deve ser rejeitado imediatamente com 401 (não cai no short-circuit)
    assert client.get("/api/home/summary").status_code == 401


def test_disable_2fa_preserves_current_session_and_revokes_old(clean_db):
    """Desativação de 2FA preserva a sessão atual do usuário e revoga tokens antigos concorrentes."""
    _, repo, _ = clean_db
    repo.create_user("lucas_2fa", "senhaForte123!", role="investidor")
    user = repo.get_by_username("lucas_2fa")
    secret, _ = repo.setup_totp(user["id"])
    from barsi.infra.security import generate_totp_code
    valid_code = generate_totp_code(secret)
    repo.confirm_and_enable_totp(user["id"], valid_code)

    token_old = create_session_token(
        user["id"], user["username"], user["role"], settings.secret_key, session_version=user.get("session_version", 1)
    )

    client = TestClient(app)
    client.cookies.set("barsi_session", token_old)

    # Desativa 2FA com código válido
    res = client.post("/api/auth/2fa/disable", json={"code": valid_code})
    assert res.status_code == 200

    # Sessão atual permanece autenticada
    res_home = client.get("/api/home/summary")
    assert res_home.status_code == 200

    # Sessão antiga em outro cliente é rejeitada
    client_other = TestClient(app)
    client_other.cookies.set("barsi_session", token_old)
    assert client_other.get("/api/home/summary").status_code == 401


def test_login_memory_rate_limit_short_circuit(clean_db):
    """Múltiplas tentativas em memória ativam bloqueio rápido antes de abrir SQLite."""
    from barsi.api.http import _login_attempts
    client = TestClient(app)
    now = time.time()
    # Simula 5 tentativas já em memória para IP testclient
    _login_attempts["testclient"] = [now - 10] * 5

    res = client.post(
        "/login",
        data={"username": "anyone", "password": "wrong"},
        follow_redirects=False
    )
    assert res.status_code == 303
    assert "error=Muitas+tentativas" in res.headers["location"]
    _login_attempts.clear()


def test_sensitive_ops_reject_deleted_user_race_condition(clean_db):
    """Operações de mudança de senha e desativação de 2FA rejeitam com 401 se usuário sumir (sem 500)."""
    _, repo, _ = clean_db
    repo.create_user("ghost_user", "senha123456", role="investidor")
    user = repo.get_by_username("ghost_user")
    token = create_session_token(
        user["id"], user["username"], user["role"],
        settings.secret_key, session_version=user.get("session_version", 1)
    )

    client = TestClient(app)
    client.cookies.set("barsi_session", token)

    # Exclui usuário do banco antes da chamada sensível
    with repo._connection() as conn:
        conn.execute("DELETE FROM usuarios WHERE id = ?", (user["id"],))
        conn.commit()

    # Tentativa de alterar senha deve retornar 401
    res_pass = client.post("/api/user/change-password", json={
        "current_password": "senha123456",
        "new_password": "nova_senha_longa_123"
    })
    assert res_pass.status_code == 401

    # Tentativa de desativar 2FA deve retornar 401
    res_2fa = client.post("/api/auth/2fa/disable", json={"code": "123456"})
    assert res_2fa.status_code == 401


