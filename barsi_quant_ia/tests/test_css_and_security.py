"""Valida a separação de CSS, bundling local do Chart.js e cabeçalhos de segurança HTTP."""
import pytest
from fastapi.testclient import TestClient
from barsi.api.http import app, is_request_secure, set_barsi_cookie
from barsi.infra.security import create_session_token
from barsi.config import settings

@pytest.fixture
def client():
    return TestClient(app)

def test_static_css_assets_exist_and_return_200(client):
    """Verifica que os arquivos CSS separados existem e são servidos estaticamente."""
    for css_file in ["theme.css", "barsi.css", "analytics.css"]:
        resp = client.get(f"/static/css/{css_file}")
        assert resp.status_code == 200, f"Falha ao servir /static/css/{css_file}"
        assert "text/css" in resp.headers.get("content-type", "")
        assert len(resp.text) > 50

def test_static_js_assets_exist_and_return_200(client):
    """Verifica que chart.umd.min.js local e analytics.js são servidos com 200 OK."""
    for js_file in ["chart.umd.min.js", "analytics.js", "common.js"]:
        resp = client.get(f"/static/js/{js_file}")
        assert resp.status_code == 200, f"Falha ao servir /static/js/{js_file}"
        assert "javascript" in resp.headers.get("content-type", "") or "text/" in resp.headers.get("content-type", "")
        assert len(resp.text) > 100

def test_security_headers_present_on_all_responses(client):
    """Verifica presença dos cabeçalhos rígidos de segurança HTTP e CSP."""
    resp = client.get("/login")
    assert resp.status_code == 200
    headers = resp.headers
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert "strict-origin-when-cross-origin" in headers.get("Referrer-Policy", "")
    assert "default-src 'self'" in headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in headers.get("Content-Security-Policy", "")
    assert "geolocation=()" in headers.get("Permissions-Policy", "")

def test_hsts_header_present_when_https_forwarded(client):
    """Verifica injeção de Strict-Transport-Security quando a requisição é sobre HTTPS (Tailscale)."""
    resp = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert resp.status_code == 200
    assert "max-age=" in resp.headers.get("Strict-Transport-Security", "")

def test_analytics_page_renders_modular_css_and_scripts(client):
    """Verifica que a página /analytics renderiza as tags de CSS modular e script local."""
    token = create_session_token(1, "admin", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)
    resp = client.get("/analytics")
    assert resp.status_code == 200
    html = resp.text
    assert "/static/css/theme.css" in html
    assert "/static/css/barsi.css" in html
    assert "/static/css/analytics.css" in html
    assert "/static/js/chart.umd.min.js" in html
    assert "/static/js/common.js" in html
    assert "/static/js/analytics.js" in html
