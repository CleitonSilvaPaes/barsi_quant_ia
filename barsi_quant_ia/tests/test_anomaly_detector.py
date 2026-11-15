"""Testes Unitários e de Integração para Sprint ML-1: Anomaly Detection em OFX.

Valida:
1. Extração determinística de características (7 dimensões).
2. Treinamento offline e compactação do Isolation Forest em NumPy (.npz < 100KB).
3. Inferência rápida em puro NumPy (< 5ms) com discriminação entre transações normais e anômalas.
4. Fallback gracioso na ausência do arquivo .npz.
5. Integração com EarlyWarningService e deduplicação de alertas.
6. Endpoint de feedback do usuário (/api/extratos/transacoes/{id}/feedback) e arquivamento de alertas.
"""

import os
import time
import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

from barsi.config import settings
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.anomaly_detector import AnomalyDetector, _c_factor
from barsi.services.early_warning_service import EarlyWarningService
from barsi.training.anomaly_trainer import train_and_export_model, generate_synthetic_financial_baseline
from barsi.infra.security import create_session_token
from barsi.api.http import app


@pytest.fixture
def test_repo(tmp_path):
    db_file = str(tmp_path / "test_anomaly.db")
    repo = BudgetRepository(db_file)
    return repo


@pytest.fixture
def trained_model_path(tmp_path):
    """Treina um modelo rápido e isolado para testes usando tmp_path."""
    db_file = str(tmp_path / "test_train.db")
    model_file = str(tmp_path / "anomaly_test_model.npz")
    train_and_export_model(
        db_path=db_file,
        output_path=model_file,
        contamination=0.05,
        n_estimators=30,  # 30 árvores para velocidade máxima em testes
    )
    return model_file


def test_c_factor_mathematical_properties():
    """Valida propriedades matemáticas do fator de correção de profundidade de Liu et al."""
    assert _c_factor(0.0) == 0.0
    assert _c_factor(1.0) == 0.0
    assert _c_factor(2.0) == 1.0
    assert _c_factor(256.0) > 1.0


def test_feature_extraction():
    """Valida que o vetor de características tem shape (7,) e tipo float32."""
    tx_dict = {
        "valor": -189.50,
        "tipo": "DEBIT",
        "data": "2026-04-15",
        "origem": "CARTAO",
        "ticker": None,
    }
    feats = AnomalyDetector.extract_features(tx_dict)
    assert feats.shape == (7,)
    assert feats.dtype.name.startswith("float")
    assert feats[1] == -1.0  # DEBIT
    assert feats[6] == 1.0  # CARTAO
    assert feats[5] == 0.0  # Sem ticker

    # Teste com ativo de investimento
    tx_div = {
        "valor": 450.00,
        "tipo": "CREDIT",
        "data": "2026-04-20",
        "origem": "CONTA",
        "ticker": "BBAS3",
    }
    feats_div = AnomalyDetector.extract_features(tx_div)
    assert feats_div[1] == 1.0  # CREDIT
    assert feats_div[5] == 1.0  # Com ticker
    assert feats_div[6] == 0.0  # CONTA


def test_detector_fallback_without_model(tmp_path):
    """Sem modelo treinado em disco, deve usar heurística resiliente sem crashar."""
    non_existent = str(tmp_path / "does_not_exist.npz")
    detector = AnomalyDetector(model_path=non_existent)
    assert detector._loaded is False

    tx_normal = {"valor": 120.0, "descricao": "ALMOCO"}
    res_normal = detector.detect(tx_normal)
    assert res_normal["is_anomaly"] is False
    assert res_normal["severity"] == "NORMAL"

    tx_outlier = {"valor": 25000.0, "descricao": "COMPRA ALTA"}
    res_outlier = detector.detect(tx_outlier)
    assert res_outlier["is_anomaly"] is True
    assert res_outlier["severity"] == "ALTA"


def test_train_and_export_compact_npz(trained_model_path):
    """Garante que o modelo treinado gera arquivo .npz menor que 100KB e com todas as árvores."""
    assert os.path.exists(trained_model_path)
    file_size_kb = os.path.getsize(trained_model_path) / 1024.0
    assert file_size_kb < 100.0  # Meta de borda para TV Box

    detector = AnomalyDetector(model_path=trained_model_path)
    assert detector._loaded is True
    assert detector._n_trees == 30


def test_inference_speed_and_discrimination(trained_model_path):
    """Garante que a inferência é sub-5ms e que outliers recebem score superior a transações usuais."""
    detector = AnomalyDetector(model_path=trained_model_path)

    tx_mercado = {"valor": 85.00, "tipo": "DEBIT", "data": "2026-05-12", "origem": "CARTAO", "descricao": "SUPERMERCADO"}
    tx_golpe = {"valor": 45000.00, "tipo": "DEBIT", "data": "2026-05-12", "origem": "CONTA", "descricao": "PIX ATIPICO MADRUGADA"}

    # Benchmark de latência
    t0 = time.perf_counter()
    for _ in range(50):
        detector.detect(tx_mercado)
    dt_ms = (time.perf_counter() - t0) / 50.0 * 1000.0
    assert dt_ms < 5.0, f"Inferência muito lenta: {dt_ms:.2f}ms (esperado < 5ms)"

    res_mercado = detector.detect(tx_mercado)
    res_golpe = detector.detect(tx_golpe)

    assert res_golpe["score"] > res_mercado["score"]
    assert res_mercado["is_anomaly"] is False


@pytest.mark.asyncio
async def test_early_warning_scan_ofx_anomalies(test_repo, trained_model_path):
    """Testa a integração da varredura com EarlyWarningService e deduplicação de alertas."""
    # Persiste uma transação normal e uma extrema no SQLite temporário
    test_repo.persist_transacoes([
        {"id_transacao": "TRN_NORM_1", "valor": 55.0, "tipo": "DEBIT", "data": "2026-05-10", "descricao": "FARMACIA", "origem": "CARTAO"},
        {"id_transacao": "TRN_ANOM_1", "valor": 95000.0, "tipo": "DEBIT", "data": "2026-05-10", "descricao": "TRANSFERENCIA ESTRANHA", "origem": "CONTA"},
    ])

    mock_notifier = MagicMock()
    mock_notifier.send_message = AsyncMock()

    service = EarlyWarningService(budget_repo=test_repo, notification_adapter=mock_notifier)
    anomalies = await service.scan_ofx_anomalies(model_path=trained_model_path)

    assert len(anomalies) >= 1
    anom = next(a for a in anomalies if a["fitid"] == "TRN_ANOM_1")
    assert anom["is_anomaly"] if "is_anomaly" in anom else anom["score"] > 0.5
    assert anom["is_new"] is True

    # Valida que o alerta foi gravado no banco de alertas
    alerts = test_repo.list_thesis_alerts(active_only=True)
    assert any("TRN_ANOM_1" in a["snippet"] for a in alerts)

    # Segunda varredura: não deve duplicar o alerta
    anomalies_second = await service.scan_ofx_anomalies(model_path=trained_model_path)
    anom_second = next(a for a in anomalies_second if a["fitid"] == "TRN_ANOM_1")
    assert anom_second["is_new"] is False


@pytest.mark.asyncio
async def test_feedback_ok_anomalia_dismisses_alert(test_repo, trained_model_path, monkeypatch):
    """Ao enviar feedback 'OK_ANOMALIA', alerta é arquivado e transação é ignorada em scans futuros."""
    test_repo.persist_transacoes([
        {"id_transacao": "TRN_CONFIRM_1", "valor": 88000.0, "tipo": "DEBIT", "data": "2026-06-01", "descricao": "COMPRA CARRO", "origem": "CONTA"},
    ])

    service = EarlyWarningService(budget_repo=test_repo)
    anomalies = await service.scan_ofx_anomalies(model_path=trained_model_path)
    assert len(anomalies) == 1

    txs = test_repo.list_transacoes()
    tx_id = txs[0]["id"]

    # Testa endpoint de feedback via TestClient
    monkeypatch.setattr(settings, "sqlite_db_path", test_repo.db_path)
    monkeypatch.setattr("barsi.api.routers.budget.require_role_action", lambda req, action_type: {"username": "admin", "role": "admin"})

    client = TestClient(app)
    resp = client.post(
        f"/api/extratos/transacoes/{tx_id}/feedback",
        json={"feedback": "OK_ANOMALIA"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Confirma que a transação recebeu o feedback
    tx_updated = test_repo.get_transacao_by_id(tx_id)
    assert tx_updated["feedback_usuario"] == "OK_ANOMALIA"

    # Confirma que o alerta ativo foi arquivado
    alerts_after = test_repo.list_thesis_alerts(active_only=True)
    assert not any("TRN_CONFIRM_1" in a["snippet"] for a in alerts_after)

    # Scan subsequente não deve mais emitir alerta para esta transação
    anomalies_after = await service.scan_ofx_anomalies(model_path=trained_model_path)
    assert not any(a["fitid"] == "TRN_CONFIRM_1" for a in anomalies_after)


def test_threshold_calibration_matches_contamination(trained_model_path):
    """Threshold deve rejeitar ~contamination% das amostras de treino."""
    detector = AnomalyDetector(model_path=trained_model_path)
    samples = generate_synthetic_financial_baseline(n_samples=1000)
    scores = [detector.score_features(AnomalyDetector.extract_features(s)) for s in samples]
    flagged = sum(1 for s in scores if s >= detector.anomaly_threshold)
    flagged_pct = flagged / len(scores) * 100.0
    assert 2.0 <= flagged_pct <= 12.0


def test_retrain_invalidates_singleton(tmp_path):
    """Garante que train_and_export_model invalida o singleton permitindo recarga automática."""
    db_file = str(tmp_path / "retrain_test.db")
    model_v1 = str(tmp_path / "model_v1.npz")
    train_and_export_model(db_path=db_file, output_path=model_v1, n_estimators=10)

    inst1 = AnomalyDetector.get_instance(model_path=model_v1)
    assert inst1.model_path == model_v1
    assert inst1.n_trees == 10

    # Retreina v2 com mais árvores
    model_v2 = str(tmp_path / "model_v2.npz")
    train_and_export_model(db_path=db_file, output_path=model_v2, n_estimators=25)

    # Novo get_instance deve recarregar v2
    inst2 = AnomalyDetector.get_instance(model_path=model_v2)
    assert inst2.model_path == model_v2
    assert inst2.n_trees == 25


@pytest.mark.asyncio
async def test_scan_ofx_anomalies_with_pydantic_objects(test_repo, trained_model_path):
    """scan_ofx_anomalies deve aceitar Transacao pydantic (fluxo de upload) sem AttributeError."""
    from barsi.services.ofx_service import Transacao
    pydantic_txs = [
        Transacao(
            id_transacao="PYD_ANOM_1",
            tipo="DEBIT",
            data="2026-05-10",
            valor=-95000.0,
            descricao="TRANSFERENCIA SUSPEITA",
            categoria="Outros",
            origem="CONTA",
        ),
    ]
    service = EarlyWarningService(budget_repo=test_repo)
    anomalies = await service.scan_ofx_anomalies(transacoes=pydantic_txs, model_path=trained_model_path)
    assert isinstance(anomalies, list)
    assert len(anomalies) >= 1
    assert anomalies[0]["fitid"] == "PYD_ANOM_1"
    assert anomalies[0]["is_anomaly"] is True


def test_api_get_extrato_anomalies(test_repo, trained_model_path, monkeypatch):
    """Testa endpoint GET /api/extratos/anomalias com inferência real."""
    test_repo.persist_transacoes([
        {"id_transacao": "API_ANOM_1", "valor": 85000.0, "tipo": "DEBIT", "data": "2026-06-01", "descricao": "PIX SUSPEITO", "origem": "CONTA"},
    ])
    monkeypatch.setattr(settings, "sqlite_db_path", test_repo.db_path)
    monkeypatch.setattr("barsi.api.routers.budget.get_current_user", lambda req: {"username": "admin", "role": "admin"})

    # Força detector a carregar modelo treinado
    from barsi.domain.anomaly_detector import AnomalyDetector
    AnomalyDetector.get_instance(model_path=trained_model_path)

    client = TestClient(app)
    resp = client.get("/api/extratos/anomalias?limit=20")
    assert resp.status_code == 200
    data = resp.json()
    assert "anomalies" in data
    assert "count" in data
    assert data["count"] >= 1
    assert any(a["fitid"] == "API_ANOM_1" for a in data["anomalies"])


def test_extratos_page_renders_anomaly_radar(test_repo, monkeypatch):
    """Garante que a página HTML /extratos renderiza o container do Radar de Anomalias e controles."""
    from barsi.adapters.history.user_repository import UserRepository
    monkeypatch.setattr(settings, "sqlite_db_path", test_repo.db_path)
    user_repo = UserRepository(test_repo.db_path)
    user_id = user_repo.create_user("admin_radar", "secret123", role="admin")
    token = create_session_token(user_id, "admin_radar", "admin", settings.secret_key)
    client = TestClient(app)
    resp = client.get("/extratos", cookies={"barsi_session": token})
    assert resp.status_code == 200
    assert "Radar de Anomalias Financeiras" in resp.text
    assert 'id="card-anomalias"' in resp.text
    assert 'id="badge-total-anomalias"' in resp.text
    assert 'id="tabela-anomalias"' in resp.text
    assert 'id="filtro-severidade-anomalias"' in resp.text
    assert "/static/js/extratos.js" in resp.text
