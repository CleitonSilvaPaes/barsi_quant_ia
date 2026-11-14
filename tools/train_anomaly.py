"""Script de Treinamento Offline de Isolation Forest para Detecção de Anomalias em OFX.

Treina o modelo com base no histórico de transações reais do SQLite (desconsiderando
as marcadas com feedback 'OK_ANOMALIA') complementadas por um baseline sintético financeiro.
Exporta a estrutura de árvores compactada (.npz) para inferência ultra-rápida em puro NumPy.

Uso:
    python tools/train_anomaly.py [--db PATH] [--output PATH] [--contamination 0.03]
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Any

import numpy as np

# Adiciona barsi_quant_ia/src ao sys.path para importações locais
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "barsi_quant_ia", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from barsi.config import settings
from barsi.domain.anomaly_detector import AnomalyDetector, _c_factor


def generate_synthetic_financial_baseline(n_samples: int = 1200) -> list[dict[str, Any]]:
    """Gera transações sintéticas representativas do perfil financeiro brasileiro.
    
    Cobre despesas rotineiras (supermercado, farmácia, transporte, contas),
    salário, proventos de investimentos e transações de cartão de crédito.
    """
    random.seed(42)
    np.random.seed(42)
    categories = [
        # (descricao, tipo, min_val, max_val, origem, chance)
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

    base_date = datetime(2026, 1, 1)
    samples: list[dict[str, Any]] = []

    for _ in range(n_samples):
        # Escolhe template com pesos
        weights = [c[5] for c in categories]
        template = random.choices(categories, weights=weights, k=1)[0]
        desc, tipo, min_v, max_v, origem, _ = template

        val = round(float(np.random.uniform(min_v, max_v)), 2)
        day_offset = random.randint(0, 360)
        dt = base_date + timedelta(days=day_offset)
        date_str = dt.strftime("%Y-%m-%d")

        ticker = None
        if "B3" in desc or "DIVIDENDO" in desc:
            ticker = random.choice(["BBAS3", "TAEE11", "ITSA4", "SAPR4", "CPLE6", "BBSE3"])

        samples.append({
            "descricao": desc,
            "tipo": tipo,
            "valor": val,
            "data": date_str,
            "origem": origem,
            "ticker": ticker,
        })

    # Adiciona algumas poucas anomalias propositais para calibração
    samples.append({"descricao": "GOLPE WHATSAPP PIX", "tipo": "DEBIT", "valor": 12500.0, "data": "2026-06-15", "origem": "CONTA", "ticker": None})
    samples.append({"descricao": "COMPRA DUPLICADA SMARTPHONE", "tipo": "DEBIT", "valor": 7899.0, "data": "2026-07-20", "origem": "CARTAO", "ticker": None})
    samples.append({"descricao": "TRANSFERENCIA ATIPICA MADRUGADA", "tipo": "DEBIT", "valor": 15000.0, "data": "2026-08-03", "origem": "CONTA", "ticker": None})

    return samples


def load_transactions_from_db(db_path: str) -> list[dict[str, Any]]:
    """Carrega transações do banco SQLite, ignorando feedback 'OK_ANOMALIA'."""
    if not os.path.exists(db_path):
        return []

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Verifica se as colunas necessárias existem
        cols = [r[1] for r in cur.execute("PRAGMA table_info(transacoes)").fetchall()]
        has_feedback = "feedback_usuario" in cols

        query = "SELECT * FROM transacoes"
        if has_feedback:
            query += " WHERE feedback_usuario IS NULL OR feedback_usuario != 'OK_ANOMALIA'"

        rows = cur.execute(query).fetchall()
        result = [dict(r) for r in rows]
        conn.close()
        return result
    except (sqlite3.Error, OSError) as e:
        print(f"Aviso: Não foi possível ler transações do banco ({e}). Usando apenas baseline.", file=sys.stderr)
        return []


def train_and_export_model(
    db_path: str,
    output_path: str,
    contamination: float = 0.05,
    n_estimators: int = 100,
) -> str:
    """Treina IsolationForest via scikit-learn e compila árvores em NumPy .npz."""
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        print("Erro: scikit-learn não está instalado no ambiente de desenvolvimento.", file=sys.stderr)
        print("Execute: pip install scikit-learn", file=sys.stderr)
        sys.exit(1)

    # 1. Carrega dados do banco e baseline sintético
    real_txs = load_transactions_from_db(db_path)
    baseline_txs = generate_synthetic_financial_baseline(n_samples=1500)
    all_txs = real_txs + baseline_txs

    print(f"Total de transações para treino: {len(all_txs)} ({len(real_txs)} do banco, {len(baseline_txs)} baseline)")

    # 2. Extrai vetor de características (7 dimensões)
    X = np.array([AnomalyDetector.extract_features(t) for t in all_txs], dtype=np.float32)

    # 3. Treina scikit-learn IsolationForest
    clf = IsolationForest(
        n_estimators=n_estimators,
        max_samples="auto",
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X)

    # 4. Extrai atributos das árvores treinadas e empacota em matrizes alinhadas
    estimators = clf.estimators_
    max_nodes = max(est.tree_.node_count for est in estimators)

    children_left = np.full((n_estimators, max_nodes), -1, dtype=np.int32)
    children_right = np.full((n_estimators, max_nodes), -1, dtype=np.int32)
    feature = np.full((n_estimators, max_nodes), -2, dtype=np.int32)
    threshold = np.zeros((n_estimators, max_nodes), dtype=np.float32)
    n_node_samples = np.zeros((n_estimators, max_nodes), dtype=np.int32)

    for i, est in enumerate(estimators):
        tree = est.tree_
        n_nodes = tree.node_count
        children_left[i, :n_nodes] = tree.children_left
        children_right[i, :n_nodes] = tree.children_right
        feature[i, :n_nodes] = tree.feature
        threshold[i, :n_nodes] = tree.threshold
        n_node_samples[i, :n_nodes] = tree.n_node_samples

    max_samples = float(clf.max_samples_)
    offset = float(clf.offset_)

    # 5. Determina o limiar de anomalia calibrado pela contaminação
    # Pontuação pelo modelo numpy compilado
    temp_detector = AnomalyDetector.__new__(AnomalyDetector)
    temp_detector._children_left = children_left
    temp_detector._children_right = children_right
    temp_detector._feature = feature
    temp_detector._threshold = threshold
    temp_detector._n_node_samples = n_node_samples
    temp_detector._max_samples = max_samples
    temp_detector._offset = offset
    temp_detector._n_trees = n_estimators
    temp_detector._c_max_samples = _c_factor(max_samples)
    temp_detector._loaded = True

    scores = np.array([temp_detector.score_features(x) for x in X])
    # O threshold padrão de anomalia é calibrado em torno de 0.58 (cobre transações raras/outliers)
    p90 = float(np.percentile(scores, 90.0))
    anomaly_threshold = round(float(min(0.58, max(0.53, p90))), 4)

    # 6. Salva .npz compactado
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    np.savez_compressed(
        output_path,
        children_left=children_left,
        children_right=children_right,
        feature=feature,
        threshold=threshold,
        n_node_samples=n_node_samples,
        max_samples=max_samples,
        offset=offset,
        anomaly_threshold=anomaly_threshold,
    )

    file_size_kb = os.path.getsize(output_path) / 1024.0
    print(f"Sucesso: Modelo compactado exportado para {output_path}")
    print(f"Tamanho do arquivo: {file_size_kb:.1f} KB")
    print(f"Árvores: {n_estimators} | Max Nodes: {max_nodes}")
    print(f"Threshold de Anomalia calibrado: {anomaly_threshold:.4f}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Treinamento offline de Isolation Forest para OFX")
    parser.add_argument("--db", default=settings.sqlite_db_path, help="Caminho para o SQLite de memória")
    mem_dir = getattr(settings, "memory_dir", None) or os.path.dirname(settings.sqlite_db_path) or "./memory"
    parser.add_argument("--output", default=os.path.join(mem_dir, "anomaly_model.npz"), help="Caminho de saída .npz")
    parser.add_argument("--contamination", type=float, default=0.05, help="Taxa estimada de anomalias (ex: 0.05 = 5%%)")
    parser.add_argument("--trees", type=int, default=100, help="Número de árvores na floresta")
    args = parser.parse_args()

    train_and_export_model(
        db_path=args.db,
        output_path=args.output,
        contamination=args.contamination,
        n_estimators=args.trees,
    )


if __name__ == "__main__":
    main()
