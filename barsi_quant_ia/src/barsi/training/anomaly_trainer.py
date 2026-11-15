"""Treinamento offline do Isolation Forest para detecção de anomalias em OFX (Sprint ML-1).

Roda no PC de desenvolvimento (requer scikit-learn, dependência opcional `dev`).
Treina com o histórico real do SQLite — excluindo transações com feedback 'OK_ANOMALIA'
— somado a um baseline sintético, e compila as árvores em matrizes NumPy (.npz)
consumidas por `barsi.domain.anomaly_detector.AnomalyDetector` na TV Box (sem sklearn).

Estrutura do .npz exportado: ver docstring de `barsi.domain.anomaly_detector`.
"""

from __future__ import annotations

import os
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np

from barsi.config import settings
from barsi.domain.anomaly_detector import AnomalyDetector, _c_factor
from barsi.infra.logging import logger

_SEED = 42
_BASELINE_SIZE = 1500

# (descricao, tipo, valor_min, valor_max, origem, peso)
_BASELINE_TEMPLATES = [
    ("SUPERMERCADO", "DEBIT", 60.0, 450.0, "CARTAO", 0.25),
    ("POSTO COMBUSTIVEL", "DEBIT", 80.0, 260.0, "CARTAO", 0.12),
    ("FARMACIA DROGARIA", "DEBIT", 25.0, 180.0, "CARTAO", 0.10),
    ("RESTAURANTE / IFOOD", "DEBIT", 35.0, 140.0, "CARTAO", 0.15),
    ("CONTA DE ENERGIA", "DEBIT", 120.0, 320.0, "CONTA", 0.05),
    ("CONTA DE INTERNET", "DEBIT", 99.0, 160.0, "CONTA", 0.05),
    ("CONDOMINIO", "DEBIT", 450.0, 900.0, "CONTA", 0.03),
    ("PAGTO SALARIO", "CREDIT", 4500.0, 9500.0, "CONTA", 0.03),
    ("RENDIMENTO POUPANCA / RDB", "CREDIT", 15.0, 150.0, "CONTA", 0.04),
    ("DIVIDENDO / JCP B3", "CREDIT", 40.0, 850.0, "CONTA", 0.08),
    ("SERVICO STREAMING", "DEBIT", 21.90, 59.90, "CARTAO", 0.05),
    ("PIX ENVIADO AMIGO", "DEBIT", 20.0, 200.0, "CONTA", 0.05),
]
_BASELINE_TICKERS = ["BBAS3", "TAEE11", "ITSA4", "SAPR4", "CPLE6", "BBSE3"]


def generate_synthetic_financial_baseline(n_samples: int = _BASELINE_SIZE) -> List[Dict[str, Any]]:
    """Gera transações sintéticas determinísticas (seed fixa) do perfil financeiro brasileiro."""
    rng = random.Random(_SEED)
    np_rng = np.random.default_rng(_SEED)
    weights = [t[5] for t in _BASELINE_TEMPLATES]
    base_date = datetime(2026, 1, 1)

    samples: List[Dict[str, Any]] = []
    for _ in range(n_samples):
        desc, tipo, min_v, max_v, origem, _w = rng.choices(_BASELINE_TEMPLATES, weights=weights, k=1)[0]
        dt = base_date + timedelta(days=rng.randint(0, 360))
        ticker = rng.choice(_BASELINE_TICKERS) if "B3" in desc else None
        samples.append({
            "descricao": desc,
            "tipo": tipo,
            "valor": round(float(np_rng.uniform(min_v, max_v)), 2),
            "data": dt.strftime("%Y-%m-%d"),
            "origem": origem,
            "ticker": ticker,
        })
    return samples


def load_transactions_from_db(db_path: str) -> List[Dict[str, Any]]:
    """Carrega transações do SQLite, ignorando as marcadas como 'OK_ANOMALIA' (falsos positivos)."""
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(transacoes)").fetchall()]
            if not cols:
                return []
            query = "SELECT * FROM transacoes"
            if "feedback_usuario" in cols:
                query += " WHERE feedback_usuario IS NULL OR feedback_usuario != 'OK_ANOMALIA'"
            return [dict(r) for r in conn.execute(query).fetchall()]
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as e:
        logger.warning("anomaly_train_db_read_failed", error=str(e))
        return []


def calibrate_threshold(scores: np.ndarray, contamination: float) -> float:
    """Limiar alinhado à contaminação: percentil (1 - contamination), com clamp configurável."""
    percentile_target = 100.0 * (1.0 - contamination)
    p_threshold = float(np.percentile(scores, percentile_target))
    clamped = min(settings.anomaly_threshold_max, max(settings.anomaly_threshold_min, p_threshold))
    return round(float(clamped), 4)


def _compile_forest(clf: Any, n_features: int) -> Dict[str, np.ndarray]:
    """Converte as árvores sklearn em matrizes alinhadas (n_trees, max_nodes).

    Remapeia índices de feature quando o bagging usa subconjunto/permutação de colunas,
    reproduzindo exatamente `IsolationForest._compute_score_samples`.
    """
    estimators = clf.estimators_
    n_trees = len(estimators)
    max_nodes = max(est.tree_.node_count for est in estimators)
    subsample_features = getattr(clf, "_max_features", n_features) != n_features

    children_left = np.full((n_trees, max_nodes), -1, dtype=np.int32)
    children_right = np.full((n_trees, max_nodes), -1, dtype=np.int32)
    feature = np.full((n_trees, max_nodes), -2, dtype=np.int32)
    threshold = np.zeros((n_trees, max_nodes), dtype=np.float32)
    n_node_samples = np.zeros((n_trees, max_nodes), dtype=np.int32)

    for i, (est, feats_idx) in enumerate(zip(estimators, clf.estimators_features_)):
        tree = est.tree_
        n = tree.node_count
        tree_feat = tree.feature.copy()
        if subsample_features:
            mask = tree_feat >= 0
            tree_feat[mask] = np.asarray(feats_idx)[tree_feat[mask]]
        children_left[i, :n] = tree.children_left
        children_right[i, :n] = tree.children_right
        feature[i, :n] = tree_feat
        threshold[i, :n] = tree.threshold
        n_node_samples[i, :n] = tree.n_node_samples

    return {
        "children_left": children_left,
        "children_right": children_right,
        "feature": feature,
        "threshold": threshold,
        "n_node_samples": n_node_samples,
    }


def train_and_export_model(
    db_path: str,
    output_path: str,
    contamination: Optional[float] = None,
    n_estimators: Optional[int] = None,
) -> Dict[str, Any]:
    """Treina o IsolationForest (sklearn) e exporta o .npz compacto para inferência NumPy.

    Retorna métricas do treino (amostras, threshold, tamanho do arquivo, % sinalizado).
    """
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise RuntimeError(
            "scikit-learn não instalado. Instale as dependências de desenvolvimento: pip install -e 'barsi_quant_ia[dev]'"
        ) from exc

    contamination = settings.anomaly_contamination if contamination is None else contamination
    n_estimators = settings.anomaly_n_estimators if n_estimators is None else n_estimators

    real_txs = load_transactions_from_db(db_path)
    baseline_txs = generate_synthetic_financial_baseline()
    all_txs = real_txs + baseline_txs
    X = np.array([AnomalyDetector.extract_features(t) for t in all_txs], dtype=np.float32)

    clf = IsolationForest(
        n_estimators=n_estimators,
        max_samples="auto",
        contamination=contamination,
        random_state=_SEED,
        n_jobs=1,
    )
    clf.fit(X)

    arrays = _compile_forest(clf, n_features=X.shape[1])
    max_samples = float(clf.max_samples_)

    # Pontua com o motor NumPy compilado (mesmo caminho da TV Box) para calibrar o limiar
    probe = AnomalyDetector.__new__(AnomalyDetector)
    probe._children_left = arrays["children_left"]
    probe._children_right = arrays["children_right"]
    probe._feature = arrays["feature"]
    probe._threshold = arrays["threshold"]
    probe._n_node_samples = arrays["n_node_samples"]
    probe._n_trees = n_estimators
    probe._c_max_samples = _c_factor(max_samples)
    probe._loaded = True
    scores = np.array([probe.score_features(x) for x in X])
    anomaly_threshold = calibrate_threshold(scores, contamination)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    np.savez_compressed(
        output_path,
        **arrays,
        max_samples=max_samples,
        offset=float(clf.offset_),
        anomaly_threshold=anomaly_threshold,
    )
    # Invalida singleton para que a API recarregue o modelo novo
    AnomalyDetector.reset_instance()

    metrics = {
        "output_path": output_path,
        "n_real": len(real_txs),
        "n_baseline": len(baseline_txs),
        "n_trees": n_estimators,
        "max_nodes": int(arrays["children_left"].shape[1]),
        "anomaly_threshold": anomaly_threshold,
        "flagged_pct": round(float((scores >= anomaly_threshold).mean() * 100.0), 2),
        "file_size_kb": round(os.path.getsize(output_path) / 1024.0, 1),
    }
    logger.info("anomaly_model_trained", **metrics)
    return metrics
