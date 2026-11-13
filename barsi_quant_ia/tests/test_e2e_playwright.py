import os
import threading
import time
import socket
import pytest
import uvicorn
from playwright.sync_api import sync_playwright
from barsi.api.http import app
from barsi.config import settings
from barsi.adapters.history.user_repository import UserRepository
from barsi.infra.security import generate_totp_code


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def e2e_server():
    port = get_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)

    yield base_url
    server.should_exit = True


def test_playwright_e2e_flow(e2e_server, isolate_database):
    base_url = e2e_server

    repo = UserRepository(isolate_database)
    repo.create_user("admin_user", "adminPass123", role="admin", email="admin.teste@example.com")
    admin = repo.get_by_username("admin_user")
    secret, _ = repo.setup_totp(admin["id"])
    code = generate_totp_code(secret)
    repo.confirm_and_enable_totp(admin["id"], code)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1. Página de Login
        page.goto(f"{base_url}/login")
        assert "Barsi Quant IA" in page.title()
        assert page.locator("h2").inner_text() == "🔐 Barsi Quant IA"
        assert page.locator('text="Continuar com Google"').is_visible()

        # 2. Testar erro de credencial incorreta
        page.fill('input[name="username"]', "admin_user")
        page.fill('input[name="password"]', "senha_errada")
        page.click('button[type="submit"]')
        page.wait_for_selector(".err")
        assert "Usuário ou senha incorretos" in page.locator(".err").inner_text()

        # 3. Login com 2FA (Fluxo automático GitHub-like)
        page.goto(f"{base_url}/login")
        page.fill('input[name="username"]', "admin.teste@example.com")
        page.fill('input[name="password"]', "adminPass123")
        page.click('button[type="submit"]')

        # Deve avançar automaticamente para a tela de 2FA
        page.wait_for_url(f"{base_url}/login/2fa*")
        assert "🔐 Autenticação 2FA" in page.locator("h2").inner_text()
        assert page.locator('input[name="totp_code"]').is_visible()

        # Preenche código 2FA e valida
        totp_val = generate_totp_code(secret)
        page.fill('input[name="totp_code"]', totp_val)
        page.click('button[type="submit"]')

        # Deve redirecionar para a Home (Cockpit)
        page.wait_for_url(f"{base_url}/")
        assert page.locator('text="Cockpit Executivo"').is_visible() or page.locator('text="Radar B.E.S.T."').is_visible()

        # 4. Navegar para o Admin e testar modal de troca de senha
        page.goto(f"{base_url}/admin")
        assert "Administração" in page.content() or page.locator("h3").first.is_visible()
        btn_senha = page.locator('button:has-text("🔑 Senha")').first
        assert btn_senha.is_visible()
        btn_senha.click()
        assert page.locator("#modal-trocar-senha").is_visible()
        page.click('button:has-text("Cancelar")')
        assert not page.locator("#modal-trocar-senha").is_visible()

        # 5. Navegar para Extratos e verificar o empty-state limpo
        page.goto(f"{base_url}/extratos")
        assert "Nenhum extrato importado ainda" in page.locator("#transacoes-body").inner_text()

        # 6. Navegar para Orçamento e verificar o empty-state limpo
        page.goto(f"{base_url}/budget")
        assert "R$ 0,00" in page.locator("#stat-entradas").inner_text()
        assert "Nenhum dado orçamentário registrado ainda" in page.locator("#budget-body").inner_text()

        browser.close()
