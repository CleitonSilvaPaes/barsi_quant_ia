"""Testes para exclusão/edição na carteira, CRUD de categorias e paginação/pesquisa de extratos."""
import pytest
from fastapi.testclient import TestClient
from barsi.api.http import app
from barsi.config import settings
from barsi.infra.security import create_session_token
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position, AcaoBarsi

@pytest.fixture
def test_client(isolate_database):
    client = TestClient(app)
    token = create_session_token(user_id=1, username="admin_teste", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)
    return client

def test_carteira_crud_single_position(test_client):
    """Testa PUT para adicionar/editar posição e DELETE para remover."""
    # 1. Adiciona ativo via PUT
    put_payload = {
        "ticker": "TAEE11",
        "preco_atual": 35.50,
        "preco_teto": 40.00,
        "div_proj": 2.40,
        "custo_medio": 34.00,
        "qtd_antes": 100,
        "teto_manual": True
    }
    res_put = test_client.put("/api/carteira/TAEE11", json=put_payload)
    assert res_put.status_code == 200
    assert res_put.json()["status"] == "ok"

    # Valida no banco
    repo = BudgetRepository(settings.sqlite_db_path)
    pos = [p for p in repo.load_positions() if p.ticker == "TAEE11"]
    assert len(pos) == 1
    assert pos[0].qtd_antes == 100
    assert pos[0].preco_teto == 40.00

    # 2. Edita margem e patrimônio via PUT
    edit_payload = {
        "ticker": "TAEE11",
        "preco_atual": 35.50,
        "margem": 20.0,
        "patrimonio": 7100.0, # 7100 / 35.50 = 200 cotas
        "feedback": "Ajuste de margem"
    }
    res_edit = test_client.put("/api/carteira/TAEE11", json=edit_payload)
    assert res_edit.status_code == 200
    pos_edited = [p for p in repo.load_positions() if p.ticker == "TAEE11"][0]
    assert pos_edited.qtd_antes == 200
    assert pos_edited.feedback_usuario == "Ajuste de margem"

    # 3. Exclui ativo via DELETE
    res_del = test_client.delete("/api/carteira/TAEE11")
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "ok"

    pos_after_del = [p for p in repo.load_positions() if p.ticker == "TAEE11"]
    assert len(pos_after_del) == 0

def test_categories_crud(test_client):
    """Testa GET, POST, PUT e DELETE em /api/categories."""
    # 1. Lista categorias iniciais
    res_list = test_client.get("/api/categories")
    assert res_list.status_code == 200
    assert "categories" in res_list.json()
    assert res_list.json()["count"] >= 1

    # 2. Cria nova categoria
    res_post = test_client.post("/api/categories", json={"name": "Pets & Veterinário", "kind": "variavel"})
    assert res_post.status_code == 200
    cat_id = res_post.json()["category"]["id"]
    assert res_post.json()["category"]["name"] == "Pets & Veterinário"

    # 3. Atualiza categoria
    res_put = test_client.put(f"/api/categories/{cat_id}", json={"name": "Pets & Saúde Animal", "kind": "variavel", "active": True})
    assert res_put.status_code == 200
    assert res_put.json()["status"] == "ok"

    # 4. Exclui categoria
    res_del = test_client.delete(f"/api/categories/{cat_id}")
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "ok"

def test_extratos_transacoes_pagination_and_search(test_client):
    """Testa listagem com busca, paginação, edição e exclusão de transação."""
    repo = BudgetRepository(settings.sqlite_db_path)
    # Insere 15 transações de teste
    for i in range(1, 16):
        desc = f"Supermercado {i}" if i % 2 == 0 else f"Combustivel Posto {i}"
        cat = "Alimentação" if i % 2 == 0 else "Transporte"
        tipo = "DEBIT"
        repo.persist_transacoes([
            {"fitid": f"TEST_FITID_{i}", "data": "2026-11-01", "descricao": desc, "categoria": cat, "tipo": tipo, "valor": 50.0 + i}
        ])

    # 1. Busca com limite de 10 por página (página 1)
    res_p1 = test_client.get("/api/extratos/transacoes?limit=10&offset=0")
    assert res_p1.status_code == 200
    data_p1 = res_p1.json()
    assert data_p1["count"] == 10
    assert data_p1["total_count"] == 15
    assert data_p1["limit"] == 10
    assert data_p1["offset"] == 0

    # 2. Busca na página 2 (offset 10)
    res_p2 = test_client.get("/api/extratos/transacoes?limit=10&offset=10")
    assert res_p2.status_code == 200
    data_p2 = res_p2.json()
    assert data_p2["count"] == 5

    # 3. Pesquisa textual
    res_search = test_client.get("/api/extratos/transacoes?search=Supermercado&limit=100")
    assert res_search.status_code == 200
    data_search = res_search.json()
    assert data_search["count"] == 7
    assert all("Supermercado" in t["descricao"] for t in data_search["transacoes"])

    # 4. Edição de transação
    t_id = data_p1["transacoes"][0]["id"]
    res_edit = test_client.put(f"/api/extratos/transacoes/{t_id}", json={
        "descricao": "Supermercado Editado",
        "categoria": "Alimentação",
        "valor": 99.90,
        "data": "2026-11-05",
        "tipo": "DEBIT"
    })
    assert res_edit.status_code == 200

    # 5. Exclusão de transação
    res_del = test_client.delete(f"/api/extratos/transacoes/{t_id}")
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "ok"
