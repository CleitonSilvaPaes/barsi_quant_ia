"""
Testes automatizados para a Sprint 4 do Roadmap Barsi Quant IA v3.0:
- PWA Instalável (Progressive Web App): manifest.json, service-worker.js, ícones e tags HTML
- Multi-Tenancy (Isolamento por Usuário): segregação de posições e metas por user_id
- Endpoints REST associados na API FastAPI
"""

import os
import json
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position
from barsi.api.http import app, STATIC_DIR, get_user_budget_repo


@pytest.fixture
def db_path(tmp_path):
    """Caminho de banco temporário isolado."""
    return str(tmp_path / "test_sprint4.db")


# =====================================================================
# 1. Testes do PWA (Progressive Web App)
# =====================================================================

def test_pwa_manifest_endpoint():
    """Valida a entrega e integridade do manifest.json do PWA."""
    client = TestClient(app)
    res = client.get("/manifest.json")
    assert res.status_code == 200
    assert "application/manifest+json" in res.headers["content-type"]

    data = res.json()
    assert data["name"] == "Barsi Quant IA — Carteira Previdenciária"
    assert data["short_name"] == "Barsi Quant"
    assert data["display"] == "standalone"
    assert data["start_url"] == "/"
    assert len(data["icons"]) >= 2


def test_pwa_service_worker_endpoint():
    """Valida a entrega e headers do Service Worker com escopo raiz."""
    client = TestClient(app)
    res = client.get("/service-worker.js")
    assert res.status_code == 200
    assert "javascript" in res.headers["content-type"]
    assert res.headers.get("Service-Worker-Allowed") == "/"
    content = res.text
    assert "addEventListener('install'" in content
    assert "addEventListener('activate'" in content
    assert "addEventListener('fetch'" in content


def test_pwa_icons_exist():
    """Garante que os arquivos de ícone nos formatos PNG e SVG estão presentes no disco."""
    icons_dir = os.path.join(STATIC_DIR, "icons")
    assert os.path.exists(os.path.join(icons_dir, "icon-192.png"))
    assert os.path.exists(os.path.join(icons_dir, "icon-512.png"))
    assert os.path.exists(os.path.join(icons_dir, "icon-192.svg"))
    assert os.path.exists(os.path.join(icons_dir, "icon-512.svg"))

    # Checa cabeçalho PNG válido
    with open(os.path.join(icons_dir, "icon-192.png"), "rb") as f:
        header = f.read(8)
        assert header == b"\x89PNG\r\n\x1a\n"


def test_base_template_has_pwa_tags():
    """Garante que o template base.html possui as meta tags de PWA e o link para o manifest."""
    base_path = os.path.join(os.path.dirname(__file__), "..", "src", "barsi", "api", "templates", "base.html")
    with open(base_path, "r", encoding="utf-8") as f:
        html = f.read()
    assert '<link rel="manifest" href="/manifest.json">' in html
    assert '<meta name="theme-color"' in html
    assert '<meta name="apple-mobile-web-app-capable"' in html
    assert 'pwa-install-btn' in html


# =====================================================================
# 2. Testes de Multi-Tenancy (Isolamento por user_id)
# =====================================================================

def test_multi_tenancy_positions_isolation(db_path):
    """Garante que dois usuários possuem carteiras completamente isoladas no mesmo SQLite."""
    repo_user1 = BudgetRepository(db_path=db_path, user_id=1)
    repo_user2 = BudgetRepository(db_path=db_path, user_id=2)

    pos1 = Position(
        row_index=1,
        ticker="TAEE11",
        empresa="Taesa",
        setor="Energia Elétrica",
        preco_atual=35.0,
        preco_teto=40.0,
        margem_seguranca=0.14,
        qtd_antes=100,
        qtd_aporte=0,
        div_proj=3.0,
        decisao="ACUMULAR",
        valor_total=3500.0
    )
    repo_user1.save_positions([pos1])

    pos2 = Position(
        row_index=1,
        ticker="BBAS3",
        empresa="Banco do Brasil",
        setor="Bancos",
        preco_atual=28.0,
        preco_teto=32.0,
        margem_seguranca=0.14,
        qtd_antes=200,
        qtd_aporte=0,
        div_proj=2.5,
        decisao="ACUMULAR",
        valor_total=5600.0
    )
    repo_user2.save_positions([pos2])

    # Verificação de isolamento
    pos_u1 = repo_user1.load_positions()
    pos_u2 = repo_user2.load_positions()

    assert len(pos_u1) == 1
    assert pos_u1[0].ticker == "TAEE11"

    assert len(pos_u2) == 1
    assert pos_u2[0].ticker == "BBAS3"

    # Exclusão do usuário 2 não afeta o usuário 1
    deleted = repo_user2.delete_position("BBAS3")
    assert deleted is True
    assert len(repo_user2.load_positions()) == 0
    assert len(repo_user1.load_positions()) == 1


def test_multi_tenancy_goals_isolation(db_path):
    """Garante que metas financeiras são segregadas estritamente por usuário."""
    repo_user1 = BudgetRepository(db_path=db_path, user_id=1)
    repo_user2 = BudgetRepository(db_path=db_path, user_id=2)

    g1_id = repo_user1.create_goal(
        tipo="renda_passiva",
        titulo="Meta Aposentadoria User 1",
        valor_alvo=5000.0,
        prazo_meses=60
    )

    g2_id = repo_user2.create_goal(
        tipo="patrimonio",
        titulo="Meta Faculdade User 2",
        valor_alvo=100000.0,
        prazo_meses=120
    )

    goals_u1 = repo_user1.list_goals()
    goals_u2 = repo_user2.list_goals()

    assert any(g["id"] == g1_id for g in goals_u1)
    assert not any(g["id"] == g2_id for g in goals_u1)

    assert any(g["id"] == g2_id for g in goals_u2)
    assert not any(g["id"] == g1_id for g in goals_u2)


def test_api_user_profile_endpoint():
    """Valida endpoint de perfil com identificador de tenancy."""
    client = TestClient(app)

    # Não autenticado -> 401
    res_anon = client.get("/api/user/profile")
    assert res_anon.status_code == 401

    # Autenticado -> 200 com user_id e username
    with patch("barsi.api.http.get_current_user", return_value={"uid": 42, "user": "investidor_pro", "role": "investidor"}):
        res = client.get("/api/user/profile")
        assert res.status_code == 200
        data = res.json()
        assert data["user_id"] == 42
        assert data["username"] == "investidor_pro"
        assert data["role"] == "investidor"


def test_api_multi_tenant_goals_via_session():
    """Valida se o endpoint REST de metas associa a nova meta ao user_id do token da sessão."""
    client = TestClient(app)

    payload = {
        "tipo": "renda_passiva",
        "titulo": "Liberdade Financeira Tenancy Test",
        "valor_alvo": 7500.0,
        "prazo_meses": 48
    }

    with patch("barsi.api.http.get_current_user", return_value={"uid": 99, "user": "membro_familia", "role": "investidor"}), \
         patch("barsi.api.http.require_role_action", return_value={"uid": 99, "user": "membro_familia", "role": "investidor"}):
        res = client.post("/api/metas", json=payload)
        assert res.status_code == 200
        goal_id = res.json()["id"]

        # Busca com o mesmo usuário -> encontra
        res_list = client.get("/api/metas")
        assert res_list.status_code == 200
        goals = res_list.json()
        assert any(g["id"] == goal_id for g in goals)

    # Busca com outro usuário (uid = 100) -> não encontra a meta do usuário 99
    with patch("barsi.api.http.get_current_user", return_value={"uid": 100, "user": "outro_usuario", "role": "investidor"}):
        res_other = client.get("/api/metas")
        assert res_other.status_code == 200
        goals_other = res_other.json()
        assert not any(g["id"] == goal_id for g in goals_other)

