"""Testes para o Sprint Segurança Push: Notificações Proativas via Telegram."""
import pytest
from starlette.testclient import TestClient

from barsi.config import settings
from barsi.adapters.history.user_repository import UserRepository
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.infra.security import (
    create_session_token,
    generate_csrf_token,
    generate_totp_code,
)
from barsi.adapters.notifications.telegram_adapter import TelegramNotifier
from barsi.api.http import app


@pytest.fixture
def sec_setup(tmp_path, monkeypatch):
    from barsi.api import http, common
    http._login_attempts.clear()
    common._login_attempts.clear()

    db_file = str(tmp_path / "test_sec_push.db")
    monkeypatch.setattr(settings, "sqlite_db_path", db_file)
    user_repo = UserRepository(db_file)
    budget_repo = BudgetRepository(db_file)

    captured_notifications = []

    async def _mock_notify(self, title: str, body: str, level: str = "info") -> bool:
        captured_notifications.append({
            "title": title,
            "body": body,
            "level": level,
        })
        return True

    monkeypatch.setattr(TelegramNotifier, "notify", _mock_notify)

    yield user_repo, budget_repo, captured_notifications

    http._login_attempts.clear()
    common._login_attempts.clear()


def test_login_brute_force_triggers_telegram_alert(sec_setup):
    """5 tentativas falhas de login disparam notificação de alerta proativo no Telegram."""
    user_repo, _, notifications = sec_setup
    user_repo.create_user("cleiton", "senhaCorreta123", role="admin")

    client = TestClient(app)

    # 4 primeiras tentativas falhas: não disparam alerta ainda
    for i in range(4):
        resp = client.post("/login", data={"username": "cleiton", "password": f"senhaErrada{i}"}, follow_redirects=False)
        assert resp.status_code == 303
        assert len(notifications) == 0

    # 5ª tentativa falha: atinge LOGIN_RATE_LIMIT_MAX e dispara alerta push imediato
    resp5 = client.post("/login", data={"username": "cleiton", "password": "senhaErrada5"}, follow_redirects=False)
    assert resp5.status_code == 303
    assert len(notifications) == 1

    alert = notifications[0]
    assert alert["title"] == "Alerta de Segurança: Múltiplas Tentativas de Login Falhas"
    assert alert["level"] == "alert"
    assert "cleiton" in alert["body"]
    assert "*Tentativas Falhas:* `5`" in alert["body"]


def test_login_2fa_rate_limit_triggers_telegram_alert(sec_setup):
    """5 tentativas falhas de código TOTP em /login/2fa disparam alerta push."""
    user_repo, _, notifications = sec_setup
    user_repo.create_user("user2fa", "senhaCorreta123", role="admin")
    user = user_repo.get_by_username("user2fa")
    secret, _ = user_repo.setup_totp(user["id"])
    user_repo.confirm_and_enable_totp(user["id"], generate_totp_code(secret))

    client = TestClient(app)

    # 1. Faz primeiro passo do login para obter o cookie de 2fa pendente
    r1 = client.post("/login", data={"username": "user2fa", "password": "senhaCorreta123"}, follow_redirects=False)
    assert r1.status_code == 303
    pending_cookie = r1.cookies.get("barsi_2fa_pending")
    assert pending_cookie is not None
    client.cookies.set("barsi_2fa_pending", pending_cookie)

    # 2. Envia 5 tentativas inválidas de TOTP
    for i in range(4):
        r_totp = client.post("/login/2fa", data={"totp_code": f"00000{i}"}, follow_redirects=False)
        assert r_totp.status_code == 303
        assert len(notifications) == 0

    r_totp5 = client.post("/login/2fa", data={"totp_code": "999999"}, follow_redirects=False)
    assert r_totp5.status_code == 303
    assert len(notifications) == 1
    alert = notifications[0]
    assert alert["title"] == "Alerta de Segurança: Múltiplas Falhas de 2FA"
    assert alert["level"] == "alert"
    assert "*Tentativas Falhas:* `5`" in alert["body"]


def test_change_password_triggers_telegram_alert(sec_setup):
    """Troca de senha mestre dispara notificação proativa com nível de warning no Telegram."""
    user_repo, _, notifications = sec_setup
    user_repo.create_user("investidor1", "senhaAntiga123", role="investidor")
    user = user_repo.get_by_username("investidor1")

    client = TestClient(app)
    token = create_session_token(user["id"], user["username"], user["role"], settings.secret_key)
    csrf = generate_csrf_token(token, settings.secret_key)

    client.cookies.set("barsi_session", token)
    client.cookies.set("barsi_csrf", csrf)

    resp = client.post(
        "/api/user/change-password",
        json={"current_password": "senhaAntiga123", "new_password": "senhaNovaSuperSegura456"},
        headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(notifications) == 1

    alert = notifications[0]
    assert alert["title"] == "Alerta de Segurança: Senha Mestre Alterada"
    assert alert["level"] == "warn"
    assert "investidor1" in alert["body"]
    assert "sessões ativas foram encerradas" in alert["body"]


def test_enable_and_disable_2fa_triggers_telegram_alerts(sec_setup):
    """Ativar e desativar 2FA disparam notificações proativas (info e crítica)."""
    user_repo, _, notifications = sec_setup
    user_repo.create_user("admin2fa", "senhaAdmin123", role="admin")
    user = user_repo.get_by_username("admin2fa")

    client = TestClient(app)
    token = create_session_token(user["id"], user["username"], user["role"], settings.secret_key)
    csrf = generate_csrf_token(token, settings.secret_key)

    client.cookies.set("barsi_session", token)
    client.cookies.set("barsi_csrf", csrf)

    # 1. Setup do TOTP
    resp_setup = client.post("/api/auth/2fa/setup", headers={"X-CSRF-Token": csrf})
    assert resp_setup.status_code == 200
    secret = resp_setup.json()["secret"]

    # 2. Ativa 2FA fornecendo código TOTP válido
    totp_code = generate_totp_code(secret)
    resp_enable = client.post(
        "/api/auth/2fa/enable",
        json={"code": totp_code},
        headers={"X-CSRF-Token": csrf}
    )
    assert resp_enable.status_code == 200
    assert len(notifications) == 1
    assert notifications[0]["title"] == "Segurança da Conta: 2FA Ativado"
    assert notifications[0]["level"] == "info"

    # 3. Desativa 2FA fornecendo código TOTP válido
    totp_code_disable = generate_totp_code(secret)
    resp_disable = client.post(
        "/api/auth/2fa/disable",
        json={"code": totp_code_disable},
        headers={"X-CSRF-Token": csrf}
    )
    assert resp_disable.status_code == 200
    assert len(notifications) == 2
    assert notifications[1]["title"] == "Alerta Crítico: 2FA Desativado"
    assert notifications[1]["level"] == "alert"
    assert "admin2fa" in notifications[1]["body"]
