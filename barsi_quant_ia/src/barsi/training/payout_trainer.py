"""Treinamento offline da Regressão Logística para Sustentabilidade de Dividendos (Sprint ML-2).

Treinado exclusivamente no PC de desenvolvimento utilizando scikit-learn.
Exporta pesos interpretáveis (w e b) em JSON leve (~1 KB) para inferência < 0.1ms na TV Box.
Incorpora aprendizado contínuo por reforço a partir de feedback do usuário (RLHF).
"""
import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Any, Optional

import numpy as np

from barsi.domain.payout_scorer import (
    PAYOUT_FEATURE_NAMES,
    PayoutScorer,
)
from barsi.domain.models import Position
from barsi.domain.quant_ai import StatisticalBarsiEngine
from barsi.infra.logging import logger

# Amostras supervisionadas canônicas dos setores B.E.S.T. vs Armadilhas de Dividendos da B3
BASE_PAYOUT_SAMPLES: List[Tuple[List[float], float, str]] = [
    # [payout, dy, debt_inv, roe, perenidade, margem, anos, vol_inv], label, ticker
    ([0.65, 0.85, 0.72, 0.75, 0.95, 0.60, 1.00, 0.55], 1.0, "BBAS3 (Banco do Brasil)"),
    ([0.60, 0.75, 0.80, 0.78, 0.95, 0.50, 1.00, 0.60], 1.0, "ITUB4 (Itaú Unibanco)"),
    ([0.80, 0.90, 0.65, 0.70, 0.98, 0.45, 0.90, 0.60], 1.0, "TAEE11 (Taesa Energia)"),
    ([0.75, 0.88, 0.70, 0.72, 0.98, 0.50, 1.00, 0.65], 1.0, "TRPL4 (ISA CTEEP)"),
    ([0.70, 0.75, 0.80, 0.68, 0.99, 0.55, 0.80, 0.65], 1.0, "SAPR4 (Sanepar)"),
    ([0.65, 0.72, 0.82, 0.70, 0.99, 0.48, 0.85, 0.62], 1.0, "CSMG3 (Copasa)"),
    ([0.85, 0.88, 0.90, 0.85, 0.95, 0.40, 0.70, 0.70], 1.0, "BBSE3 (BB Seguridade)"),
    ([0.80, 0.85, 0.92, 0.80, 0.95, 0.38, 0.60, 0.68], 1.0, "CXSE3 (Caixa Seguridade)"),
    ([0.70, 0.78, 0.75, 0.65, 0.92, 0.42, 0.90, 0.60], 1.0, "VIVT3 (Telefônica Brasil)"),
    ([0.75, 0.82, 0.68, 0.65, 0.98, 0.46, 0.80, 0.58], 1.0, "CPLE6 (Copel Energia)"),
    # Dividend traps, empresas ciclicas sem margem ou em reestruturação
    ([0.10, 0.15, 0.20, 0.10, 0.70, -0.40, 0.20, 0.35], 0.0, "OIBR3 (Recuperação Judicial)"),
    ([0.20, 0.20, 0.25, 0.05, 0.70, -0.25, 0.30, 0.30], 0.0, "IRBR3 (Corte severo / sinistros)"),
    ([0.95, 0.30, 0.30, 0.20, 0.60, -0.30, 0.10, 0.30], 0.0, "CVCB3 (Cíclica com endividamento)"),
    ([0.05, 0.05, 0.15, 0.05, 0.60, -0.50, 0.10, 0.25], 0.0, "AMER3 (Crise contábil / fraude)"),
    ([0.95, 0.45, 0.35, 0.30, 0.65, 0.10, 0.20, 0.40], 0.0, "BRKM5 (Petroquímica em baixa de ciclo)"),
]


def extract_rlhf_samples_from_db(db_path: str) -> Tuple[List[List[float]], List[float], int]:
    """Extrai features e rótulos supervisionados do histórico de análises a partir de feedbacks reais."""
    if not db_path or not os.path.exists(db_path):
        return [], [], 0

    X_rlhf: List[List[float]] = []
    y_rlhf: List[float] = []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Busca último feedback explícito por ativo registrado
        rows = conn.execute("""
            SELECT h.ticker, h.preco, h.preco_teto, h.margem_seguranca,
                   h.feedback_usuario, p.setor, p.div_proj
              FROM historico_analises h
              JOIN (
                  SELECT ticker, MAX(id) as max_id 
                    FROM historico_analises 
                   WHERE feedback_usuario != '' AND feedback_usuario IS NOT NULL
                   GROUP BY ticker
              ) latest ON h.id = latest.max_id
              LEFT JOIN positions p ON p.ticker = h.ticker
              ORDER BY h.id ASC
        """).fetchall()

        for row in rows:
            feedback = str(row["feedback_usuario"] or "").strip().upper()
            if "CONCORDO" in feedback:
                label = 1.0
            elif "DISCORDO" in feedback:
                label = 0.0
            else:
                continue

            preco = float(row["preco"] or 1.0)
            margem = float(row["margem_seguranca"] or 0.0)
            setor = str(row["setor"] or "OUTROS").strip().upper()
            div_proj = float(row["div_proj"] or 0.0) if row["div_proj"] else (preco * 0.06 if preco > 0 else 1.0)

            perenidade = StatisticalBarsiEngine.SETOR_STATS.get(
                setor,
                StatisticalBarsiEngine.SETOR_STATS.get("OUTROS", {"perenidade": 0.70})
            ).get("perenidade", 0.85)

            pos = Position(
                ticker=row["ticker"],
                row_index=0,
                empresa=row["ticker"],
                setor=setor,
                qtd_antes=0,
                qtd_aporte=0,
                qtd_total=0,
                preco_atual=preco,
                div_proj=div_proj,
                preco_teto=float(row["preco_teto"] or (div_proj / 0.06 if div_proj > 0 else 0.0)),
                margem_seguranca=margem,
            )
            from barsi.domain.payout_scorer import extract_position_features
            feats = extract_position_features(pos, extras={"perenidade": perenidade})
            X_rlhf.append(feats)
            y_rlhf.append(label)
    finally:
        conn.close()

    return X_rlhf, y_rlhf, len(y_rlhf)


def train_and_export_payout_model(
    db_path: Optional[str] = None,
    output_path: str = "memory/payout_weights.json",
    c_param: float = 1.0,
    seed: int = 42,
) -> Dict[str, Any]:
    """Treina Regressão Logística com regularização L2, exporta JSON interpretável e reseta singletons."""
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, log_loss
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "scikit-learn não instalado. Rode no PC de desenvolvimento: "
            "pip install -e 'barsi_quant_ia[dev]'"
        ) from exc

    X_list: List[List[float]] = [sample[0] for sample in BASE_PAYOUT_SAMPLES]
    y_list: List[float] = [sample[1] for sample in BASE_PAYOUT_SAMPLES]

    rlhf_count = 0
    if db_path and os.path.exists(db_path):
        try:
            x_rlhf, y_rlhf, rlhf_count = extract_rlhf_samples_from_db(db_path)
            X_list.extend(x_rlhf)
            y_list.extend(y_rlhf)
            if rlhf_count > 0:
                logger.info("payout_training_rlhf_incorporated", count=rlhf_count)
        except Exception as e:
            logger.warning("payout_training_rlhf_failed", error=str(e))

    X = np.array(X_list, dtype=np.float64)
    y = np.array(y_list, dtype=np.float64)

    # Regressão Logística com solver L-BFGS e regularização L2 via parâmetro C
    model = LogisticRegression(
        C=c_param,
        solver="lbfgs",
        class_weight="balanced",
        max_iter=1000,
        random_state=seed,
    )
    model.fit(X, y)

    y_pred_proba = model.predict_proba(X)[:, 1]
    y_pred = (y_pred_proba >= 0.5).astype(int)
    acc = float(accuracy_score(y, y_pred))
    loss = float(log_loss(y, y_pred_proba))

    weights = [round(float(w), 4) for w in model.coef_[0]]
    intercept = round(float(model.intercept_[0]), 4)

    metrics = {
        "accuracy": round(acc, 4),
        "log_loss": round(loss, 4),
        "n_samples": int(len(X)),
        "rlhf_samples": int(rlhf_count),
        "c_param": float(c_param),
    }

    feature_summary = {
        name: {"weight": w, "direction": "FAVORAVEL" if w > 0 else "DESFAVORAVEL"}
        for name, w in zip(PAYOUT_FEATURE_NAMES, weights)
    }

    output_data = {
        "model": "payout_sustainability_v2",
        "algorithm": "LogisticRegression(L2)",
        "feature_names": PAYOUT_FEATURE_NAMES,
        "weights": weights,
        "intercept": intercept,
        "thresholds": {"alta": 0.70, "media": 0.40},
        "feature_summary": feature_summary,
        "metrics": metrics,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }

    dir_name = os.path.dirname(output_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    # Backup se já existir
    if os.path.exists(output_path):
        backup = output_path.replace(".json", "_prev.json")
        try:
            os.replace(output_path, backup)
        except Exception:
            pass

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    # Invalida singletons para que novas inferências carreguem os novos pesos
    PayoutScorer.reset_instance()
    try:
        from barsi.domain.quant_ai import DividendSustainabilityScorer
        DividendSustainabilityScorer.reload()
    except Exception:
        pass

    logger.info("payout_model_trained_successfully", path=output_path, metrics=metrics)
    return output_data
