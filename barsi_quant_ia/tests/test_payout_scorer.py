"""Testes Unitários e de Integração para o Sprint ML-2: Payout Sustainability v2 (RLHF).

Valida:
1. Extração determinística de características (8 dimensões normalizadas).
2. Treinamento offline com Regressão Logística e exportação de JSON interpretável (< 2 KB).
3. Inferência rápida pura em Python/NumPy (< 1ms) com discriminação entre setores B.E.S.T. e cortes.
4. Interpretabilidade de contribuição de features (pesos explicáveis w e b).
5. Incorporação de feedback humano (RLHF) a partir de historico_analises.
6. Invalidação de singleton na re-calibração.
7. Retrocompatibilidade da interface DividendSustainabilityScorer.
"""
import os
import time
import sqlite3
import pytest
from typer.testing import CliRunner
from fastapi.testclient import TestClient
from barsi.api.http import app

from barsi.domain.models import Position, AcaoBarsi
from barsi.domain.payout_scorer import (
    PayoutScorer,
    extract_position_features,
    PAYOUT_FEATURE_NAMES,
)
from barsi.domain.quant_ai import DividendSustainabilityScorer
from barsi.training.payout_trainer import (
    train_and_export_payout_model,
    extract_rlhf_samples_from_db,
)
from barsi.__main__ import app as cli_app


@pytest.fixture
def sample_position_bbas3():
    return Position(
        ticker="BBAS3",
        row_index=1,
        empresa="Banco do Brasil",
        setor="Bancos",
        qtd_antes=100,
        qtd_aporte=20,
        qtd_total=120,
        preco_atual=28.50,
        div_proj=2.85,
        preco_teto=47.50,
        margem_seguranca=66.6,
        status_barsi=AcaoBarsi.ACUMULAR,
    )


@pytest.fixture
def sample_position_distressed():
    return Position(
        ticker="OIBR3",
        row_index=2,
        empresa="Oi S.A.",
        setor="Telecomunicações",
        qtd_antes=10,
        qtd_aporte=0,
        qtd_total=10,
        preco_atual=0.80,
        div_proj=0.01,
        preco_teto=0.16,
        margem_seguranca=-80.0,
        status_barsi=AcaoBarsi.AGUARDAR,
    )


def test_feature_extraction_normalized(sample_position_bbas3):
    """Garante que as 8 features são extraídas dentro dos intervalos numéricos esperados."""
    feats = extract_position_features(
        sample_position_bbas3,
        extras={
            "lucro_estimado": 5.0,
            "deb_ebitda": 1.2,
            "roe": 0.22,
            "perenidade": 0.95,
            "anos_pagando": 10.0,
            "vol_anual": 0.25,
        }
    )
    assert len(feats) == len(PAYOUT_FEATURE_NAMES)
    assert len(feats) == 8
    # Todas as features normalizadas devem estar no intervalo [ -0.5, 2.0 ]
    for f in feats:
        assert isinstance(f, float)
        assert -0.5 <= f <= 2.0


def test_payout_scorer_fallback_without_weights(sample_position_bbas3, tmp_path):
    """Na ausência do arquivo de pesos, o scorer usa o baseline conservador com is_loaded=False."""
    missing_path = str(tmp_path / "nao_existe.json")
    scorer = PayoutScorer(weights_path=missing_path)
    assert scorer.is_loaded is False

    res = scorer.score(sample_position_bbas3, extras={"perenidade": 0.95})
    assert res["is_loaded"] is False
    assert 0.0 <= res["score"] <= 1.0
    assert res["label"] in ("ALTA", "MÉDIA", "BAIXA")
    assert res["badge"] in ("🟢", "🟡", "🔴")
    assert "contributions" in res
    assert len(res["contributions"]) == 8


def test_train_and_export_payout_model(tmp_path):
    """Treinamento offline deve gerar JSON compacto (< 5KB), com acurácia alta e pesos interpretáveis."""
    output_json = str(tmp_path / "payout_weights_test.json")
    model_data = train_and_export_payout_model(output_path=output_json, c_param=1.0)

    assert os.path.exists(output_json)
    file_size_kb = os.path.getsize(output_json) / 1024.0
    assert file_size_kb < 5.0  # Meta: < 5 KB

    assert "weights" in model_data
    assert len(model_data["weights"]) == 8
    assert "intercept" in model_data
    assert "metrics" in model_data
    assert model_data["metrics"]["accuracy"] >= 0.80

    # Carrega pesos no scorer
    scorer = PayoutScorer(weights_path=output_json)
    assert scorer.is_loaded is True
    assert len(scorer.weights) == 8


def test_payout_inference_speed_and_discrimination(sample_position_bbas3, sample_position_distressed, tmp_path):
    """Inferência deve ser sub-milissegundo e discriminar categoricamente BBAS3 de OIBR3."""
    output_json = str(tmp_path / "payout_model.json")
    train_and_export_payout_model(output_path=output_json)
    scorer = PayoutScorer(weights_path=output_json)

    # 1. Benchmark de latência (1000 avaliações)
    t0 = time.perf_counter()
    for _ in range(1000):
        scorer.score(sample_position_bbas3, extras={"perenidade": 0.95})
    elapsed_ms = (time.perf_counter() - t0) * 1000.0 / 1000.0
    assert elapsed_ms < 1.0, f"Inferência muito lenta: {elapsed_ms:.4f} ms (meta: < 1.0ms)"

    # 2. Discriminação
    res_bbas3 = scorer.score(sample_position_bbas3, extras={"perenidade": 0.95, "roe": 0.21, "deb_ebitda": 1.0, "anos_pagando": 10.0})
    res_oibr3 = scorer.score(sample_position_distressed, extras={"perenidade": 0.70, "roe": 0.02, "deb_ebitda": 6.0, "anos_pagando": 1.0})

    assert res_bbas3["score"] >= 0.60
    assert res_bbas3["label"] in ("ALTA", "MÉDIA")
    assert res_bbas3["badge"] == "🟢"

    assert res_oibr3["score"] < 0.35
    assert res_oibr3["label"] == "BAIXA"
    assert res_oibr3["badge"] == "🔴"


def test_payout_explicability_contributions(sample_position_bbas3, tmp_path):
    """Valida que a soma das contribuições mais intercept resulta no logit exato."""
    output_json = str(tmp_path / "payout_model.json")
    train_and_export_payout_model(output_path=output_json)
    scorer = PayoutScorer(weights_path=output_json)

    res = scorer.score(sample_position_bbas3, extras={"perenidade": 0.95})
    contributions = res["contributions"]
    intercept = res["intercept"]
    logit = res["logit"]

    # logit deve ser aproximadamente intercept + soma(contributions)
    contrib_sum = sum(contributions.values())
    assert abs((intercept + contrib_sum) - logit) < 0.05

    # Para BBAS3 com perenidade 0.95, a contribuição de perenidade_setor deve ser francamente positiva
    assert contributions["perenidade_setor"] > 0.0


def test_retrain_uses_rlhf_from_sqlite(tmp_path):
    """Garante que feedbacks 'CONCORDO' e 'DISCORDO' em historico_analises são consumidos pelo treino."""
    db_file = str(tmp_path / "rlhf_test.db")
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE historico_analises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                preco REAL,
                preco_teto REAL,
                margem_seguranca REAL,
                feedback_usuario TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE positions (
                ticker TEXT PRIMARY KEY,
                setor TEXT,
                div_proj REAL
            )
        """)
        # Insere dados de posições e feedbacks do investidor
        conn.execute("INSERT INTO positions VALUES ('TAEE11', 'Energia Elétrica', 2.30)")
        conn.execute("INSERT INTO positions VALUES ('CVCB3', 'Turismo', 0.10)")

        conn.execute("INSERT INTO historico_analises (ticker, preco, preco_teto, margem_seguranca, feedback_usuario) VALUES ('TAEE11', 35.0, 45.0, 28.5, 'CONCORDO - EXCELENTE ATIVO')")
        conn.execute("INSERT INTO historico_analises (ticker, preco, preco_teto, margem_seguranca, feedback_usuario) VALUES ('CVCB3', 3.0, 2.0, -33.3, 'DISCORDO - NÃO QUERO CARTEIRA')")
        conn.commit()

    X_rlhf, y_rlhf, count = extract_rlhf_samples_from_db(db_file)
    assert count == 2
    assert set(y_rlhf) == {0.0, 1.0}

    output_json = str(tmp_path / "model_rlhf.json")
    res = train_and_export_payout_model(db_path=db_file, output_path=output_json)
    assert res["metrics"]["rlhf_samples"] == 2
    assert res["metrics"]["n_samples"] > 15


def test_retrain_invalidates_payout_singleton(tmp_path, sample_position_bbas3):
    """Garante que novo treinamento invalida o singleton e recarrega os pesos."""
    model_v1 = str(tmp_path / "model_v1.json")
    train_and_export_payout_model(output_path=model_v1, c_param=0.1)

    scorer1 = PayoutScorer.get_instance(weights_path=model_v1)
    w1 = list(scorer1.weights)

    # Treina v2 com hiperparâmetro diferente
    model_v2 = str(tmp_path / "model_v2.json")
    train_and_export_payout_model(output_path=model_v2, c_param=10.0)

    scorer2 = PayoutScorer.get_instance(weights_path=model_v2)
    w2 = list(scorer2.weights)

    assert scorer2.weights_path == model_v2
    # Pesos devem ter variado devido ao C de regularização diferente
    assert w1 != w2


def test_dividend_sustainability_scorer_integration(sample_position_bbas3, tmp_path):
    """Garante que a interface clássica DividendSustainabilityScorer funciona usando o motor v2."""
    output_json = str(tmp_path / "dss_v2.json")
    train_and_export_payout_model(output_path=output_json)

    # Configura instância com os pesos novos
    dss = DividendSustainabilityScorer(weights_path=output_json)
    res = dss.score(sample_position_bbas3, extras={"perenidade": 0.95})

    assert "score" in res
    assert "label" in res
    assert "badge" in res
    assert res["label"] in ("ALTA", "MÉDIA", "BAIXA")
    assert res["model_version"] == "v2_logistic_regression"

    # Caso provento zerado -> BAIXA
    zero_div_pos = Position(**{**sample_position_bbas3.model_dump(), "div_proj": 0.0})
    res_zero = dss.score(zero_div_pos)
    assert res_zero["score"] == 0.0
    assert res_zero["label"] == "BAIXA"


def test_cli_ml_retrain_payout(tmp_path):
    """Valida o comando CLI 'python -m barsi ml-retrain-payout'."""
    runner = CliRunner()
    output_file = str(tmp_path / "cli_payout.json")
    result = runner.invoke(cli_app, ["ml-retrain-payout", "--output", output_file])
    assert result.exit_code == 0
    assert "Treinando modelo de sustentabilidade de dividendos v2" in result.output
    assert "Modelo exportado" in result.output
    assert "Explicabilidade Barsi" in result.output
    assert os.path.exists(output_file)


def test_ml_retrain_payout_cli_import_error_message(monkeypatch, tmp_path):
    """CLI / trainer deve emitir mensagem amigável se sklearn ausente (não ModuleNotFoundError cru)."""
    import builtins
    original_import = builtins.__import__

    def _block_sklearn(name, *args, **kwargs):
        if name.startswith("sklearn"):
            raise ImportError("No module named 'sklearn'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_sklearn)
    out_file = str(tmp_path / "never.json")
    with pytest.raises(RuntimeError, match="scikit-learn não instalado"):
        train_and_export_payout_model(output_path=out_file)


def test_api_payout_score_endpoint(isolate_database):
    """Valida o endpoint GET /api/payout/score/{ticker} com explicabilidade completa."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    from barsi.api.common import invalidate_canonical_positions_cache
    repo = BudgetRepository(isolate_database)
    repo.persist_positions([
        Position(
            ticker="TAEE11",
            row_index=10,
            empresa="Taesa Transmissora",
            setor="Energia Elétrica",
            qtd_antes=200,
            qtd_aporte=50,
            qtd_total=250,
            preco_atual=35.50,
            div_proj=3.20,
            preco_teto=53.33,
            margem_seguranca=50.0,
        )
    ])
    invalidate_canonical_positions_cache()

    client = TestClient(app)

    # 1. Sem autenticação deve retornar 401
    resp_unauth = client.get("/api/payout/score/TAEE11")
    assert resp_unauth.status_code == 401

    # Autentica o cliente
    from barsi.infra.security import create_session_token
    from barsi.config import settings
    token = create_session_token(user_id=1, username="admin_test", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)

    # 2. Sucesso com explicabilidade
    resp = client.get("/api/payout/score/TAEE11")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticker"] == "TAEE11"
    assert data["empresa"] == "Taesa Transmissora"
    assert data["setor"] == "Energia Elétrica"
    assert 0.0 <= data["score"] <= 1.0
    assert data["label"] in ("ALTA", "MÉDIA", "BAIXA")
    assert data["badge"] in ("🟢", "🟡", "🔴")
    assert "contributions" in data
    assert len(data["contributions"]) == 8
    assert "feature_summary" in data
    assert "logit" in data
    assert "intercept" in data

    # 3. Ativo inexistente retorna 404
    resp_404 = client.get("/api/payout/score/INEXISTENTE_999")
    assert resp_404.status_code == 404
    assert "não encontrado na carteira" in resp_404.json()["detail"]


def test_payout_endpoint_uses_sector_perenidade(isolate_database):
    """TAEE11 (Energia Elétrica) deve classificar como ENERGIA e usar perenidade 0.98, não 0.70 (OUTROS)."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    from barsi.api.common import invalidate_canonical_positions_cache
    from barsi.infra.security import create_session_token
    from barsi.config import settings

    repo = BudgetRepository(isolate_database)
    repo.persist_positions([
        Position(
            ticker="TAEE11",
            row_index=10,
            empresa="Taesa Transmissora",
            setor="Energia Elétrica",
            preco_atual=35.5,
            div_proj=3.2,
            preco_teto=53.33,
            margem_seguranca=50.0,
        )
    ])
    invalidate_canonical_positions_cache()

    client = TestClient(app)
    token = create_session_token(1, "admin", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    data = client.get("/api/payout/score/TAEE11").json()
    assert data["setor_classificado"] == "ENERGIA"
    # Com perenidade 0.98, contributions['perenidade_setor'] é ponderada por 0.98 (muito acima de 0.70)
    assert data["contributions"]["perenidade_setor"] >= 1.8


def test_carteira_template_includes_payout_ml2_widget(isolate_database):
    """Template carteira.html deve conter o card e elementos visuais do Payout Scorer ML-2 no drawer."""
    from barsi.infra.security import create_session_token
    from barsi.config import settings

    client = TestClient(app)
    token = create_session_token(1, "admin", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    resp = client.get("/carteira")
    assert resp.status_code == 200
    html = resp.text

    assert "drawer-payout-card" in html
    assert "drawer-payout-score" in html
    assert "drawer-payout-bars" in html
    assert "drawer-payout-model-badge" in html
    assert "drawer-payout-logit" in html
    assert "Sustentabilidade Proventos (ML-2)" in html


def test_insights_template_includes_payout_ml2_modal(isolate_database):
    """Template insights.html deve conter o modal interativo com barras de contribuição ML-2."""
    from barsi.infra.security import create_session_token
    from barsi.config import settings

    client = TestClient(app)
    token = create_session_token(1, "admin", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    resp = client.get("/insights")
    assert resp.status_code == 200
    html = resp.text

    assert "modal-payout-insights" in html
    assert "modal-payout-bars" in html
    assert "modal-payout-score" in html
    assert "modal-payout-status-modelo" in html
    assert "Decomposição dos Fatores" in html



