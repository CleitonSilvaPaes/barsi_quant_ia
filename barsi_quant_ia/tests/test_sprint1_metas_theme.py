"""Testes automatizados da Sprint 1 do Roadmap Barsi Quant IA v3.0:
- Modo Claro / Escuro (Theme Toggle, Anti-FOUC, CSS Tokens)
- Dashboard de Metas & Objetivos Financeiros (CRUD SQLite, Cálculo de Progresso em Tempo Real, Rotas HTTP & API)
"""
import os
import json
import pytest
from fastapi.testclient import TestClient

from barsi.domain.models import Position, DividendReceived
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.adapters.history.user_repository import UserRepository
from barsi.infra.security import create_session_token
from barsi.api.http import app
from barsi.config import settings

client = TestClient(app)


def test_budget_repository_goals_crud(tmp_path):
    """Valida ciclo de vida completo de metas no SQLite em banco temporário isolado."""
    db_file = str(tmp_path / "test_goals.db")
    repo = BudgetRepository(db_file)

    # 1. Verifica se as metas padrão foram semeadas
    goals = repo.list_goals()
    assert len(goals) >= 3
    tipos = {g["tipo"] for g in goals}
    assert "renda_passiva" in tipos
    assert "patrimonio" in tipos
    assert "acoes_ticker" in tipos

    # 2. Criação de nova meta válida
    new_id = repo.create_goal(
        tipo="renda_passiva",
        titulo="Meta de Liberdade (R$ 10k/mês)",
        valor_alvo=10000.0,
        prazo_meses=60,
        notas="Independência financeira plena com dividendos"
    )
    assert new_id > 0

    # 3. Validação de regras de negócio (tipo inválido e valor <= 0)
    with pytest.raises(ValueError, match="Tipo de meta inválido"):
        repo.create_goal(tipo="invalido", titulo="Teste", valor_alvo=100.0)

    with pytest.raises(ValueError, match="maior que zero"):
        repo.create_goal(tipo="patrimonio", titulo="Teste", valor_alvo=-50.0)

    # 4. Atualização de meta
    ok_up = repo.update_goal(
        goal_id=new_id,
        tipo="renda_passiva",
        titulo="Meta de Liberdade (R$ 12k/mês)",
        valor_alvo=12000.0,
        prazo_meses=72,
        notas="Ajuste pela inflação"
    )
    assert ok_up is True

    # Verifica se os dados foram atualizados
    updated = next(g for g in repo.list_goals() if g["id"] == new_id)
    assert updated["titulo"] == "Meta de Liberdade (R$ 12k/mês)"
    assert updated["valor_alvo"] == 12000.0
    assert updated["prazo_meses"] == 72

    # 5. Exclusão de meta
    ok_del = repo.delete_goal(new_id)
    assert ok_del is True
    assert not any(g["id"] == new_id for g in repo.list_goals())


def test_budget_repository_goals_progress_calculation(tmp_path):
    """Valida cálculo de progresso em tempo real das metas com base em dados canônicos do SQLite."""
    db_file = str(tmp_path / "test_goals_calc.db")
    repo = BudgetRepository(db_file)

    # Limpa metas padrão para testar cenários pontuais
    with repo._conn() as conn:
        conn.execute("DELETE FROM financial_goals")

    # Cadastra posições canônicas
    p1 = Position(
        ticker="BBAS3",
        row_index=1,
        empresa="Banco do Brasil",
        setor="Bancos",
        qtd_antes=800,
        qtd_aporte=200,
        preco_atual=28.0,
        div_proj=2.80,
        preco_teto=46.66,
        margem_seguranca=66.6,
        valor_total=28000.0
    )
    p2 = Position(
        ticker="TAEE11",
        row_index=2,
        empresa="Taesa",
        setor="Energia",
        qtd_antes=1000,
        qtd_aporte=0,
        preco_atual=35.0,
        div_proj=3.50,
        preco_teto=58.33,
        margem_seguranca=66.6,
        valor_total=35000.0
    )
    repo.save_positions([p1, p2])

    # Cria metas para testar:
    # Meta 1: Renda Passiva de R$ 500/mês
    # Total de dividendos anuais projetados: (1000 * 2.80) + (1000 * 3.50) = 2800 + 3500 = 6300 / ano -> 525/mês
    g1 = repo.create_goal(tipo="renda_passiva", titulo="Primeiros R$ 500/mês", valor_alvo=500.0)

    # Meta 2: Patrimônio de R$ 100.000 (Atual é 28.000 + 35.000 = 63.000 -> 63%)
    g2 = repo.create_goal(tipo="patrimonio", titulo="R$ 100k Acumulados", valor_alvo=100000.0)

    # Meta 3: 1.000 ações de BBAS3 (Atual é 800 + 200 = 1000 -> 100%)
    g3 = repo.create_goal(tipo="acoes_ticker", titulo="1.000 BBAS3", valor_alvo=1000.0, ticker="BBAS3")

    # Executa o cálculo consolidado
    results = repo.get_goals_with_progress()
    res_map = {r["id"]: r for r in results}

    # Avalia Meta 1 (Renda Passiva)
    m1 = res_map[g1]
    assert m1["valor_atual"] >= 525.0
    assert m1["progresso_pct"] == 100.0
    assert m1["concluida"] is True
    assert m1["restante"] == 0.0

    # Avalia Meta 2 (Patrimônio)
    m2 = res_map[g2]
    assert m2["valor_atual"] == 63000.0
    assert m2["progresso_pct"] == 63.0
    assert m2["concluida"] is False
    assert m2["restante"] == 37000.0

    # Avalia Meta 3 (Ações Ticker)
    m3 = res_map[g3]
    assert m3["valor_atual"] == 1000.0
    assert m3["progresso_pct"] == 100.0
    assert m3["concluida"] is True
    assert m3["restante"] == 0.0


def test_api_metas_http_lifecycle(tmp_path, monkeypatch):
    """Valida as rotas HTTP e API de Metas (Página HTML, GET/POST/PUT/DELETE)."""
    db_file = str(tmp_path / "test_api_metas.db")
    monkeypatch.setattr(settings, "sqlite_db_path", db_file)

    # Cria usuário admin para autenticação
    user_repo = UserRepository(db_file)
    user_repo.create_user("investidor_teste", "senhaSegura123", role="admin")
    u = user_repo.get_by_username("investidor_teste")

    session_token = create_session_token(
        user_id=u["id"],
        username=u["username"],
        role=u["role"],
        secret_key=settings.secret_key
    )
    cookies = {"barsi_session": session_token}

    # 1. Acesso à página HTML /metas
    resp_page = client.get("/metas", cookies=cookies)
    assert resp_page.status_code == 200
    assert "Dashboard de Metas & Objetivos" in resp_page.text
    assert "btn-theme-toggle" in resp_page.text
    assert 'class="nav-item active"' in resp_page.text  # NAV_METAS ativa

    # 2. Requisição sem autenticação deve retornar 401
    resp_unauth = client.get("/api/metas")
    assert resp_unauth.status_code == 401

    # 3. GET /api/metas autenticado
    resp_get = client.get("/api/metas", cookies=cookies)
    assert resp_get.status_code == 200
    metas = resp_get.json()
    assert isinstance(metas, list)
    assert len(metas) >= 3

    # 4. POST /api/metas para criar nova meta
    payload = {
        "tipo": "aporte_mensal",
        "titulo": "Aporte Mensal R$ 3.000",
        "valor_alvo": 3000.0,
        "prazo_meses": 12,
        "notas": "Foco em manter disciplina"
    }
    resp_post = client.post("/api/metas", json=payload, cookies=cookies)
    assert resp_post.status_code == 200
    created = resp_post.json()
    assert created["status"] == "ok"
    meta_id = created["id"]

    # 5. PUT /api/metas/{id} para atualizar meta
    payload_edit = {
        "tipo": "aporte_mensal",
        "titulo": "Aporte Mensal R$ 4.000",
        "valor_alvo": 4000.0,
        "prazo_meses": 12,
        "notas": "Aumento de capacidade de poupança"
    }
    resp_put = client.put(f"/api/metas/{meta_id}", json=payload_edit, cookies=cookies)
    assert resp_put.status_code == 200
    assert resp_put.json()["status"] == "ok"

    # 6. DELETE /api/metas/{id}
    resp_del = client.delete(f"/api/metas/{meta_id}", cookies=cookies)
    assert resp_del.status_code == 200
    assert resp_del.json()["status"] == "ok"

    # Confirma que foi excluída
    resp_get_after = client.get("/api/metas", cookies=cookies)
    assert not any(m["id"] == meta_id for m in resp_get_after.json())


def test_theme_toggle_assets_and_markup():
    """Valida presença dos componentes estruturais do Modo Claro/Escuro."""
    templates_dir = os.path.join(os.path.dirname(__file__), "..", "src", "barsi", "api", "templates")
    base_file = os.path.join(templates_dir, "base.html")
    with open(base_file, "r", encoding="utf-8") as f:
        base_html = f.read()

    # Tokens de tema e prevenção de FOUC
    assert 'data-theme' in base_html
    assert '[data-theme="light"]' in base_html
    assert 'barsi_theme' in base_html
    assert 'btn-theme-toggle' in base_html
    assert 'toggleTheme()' in base_html

    # Script JavaScript comum
    static_js_file = os.path.join(os.path.dirname(__file__), "..", "src", "barsi", "api", "static", "js", "common.js")
    with open(static_js_file, "r", encoding="utf-8") as f:
        common_js = f.read()

    assert 'function toggleTheme()' in common_js
    assert 'function updateThemeIcon(' in common_js
    assert 'barsi_theme' in common_js
