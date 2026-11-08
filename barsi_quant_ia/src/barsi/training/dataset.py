"""Coleta dados de treinamento do próprio histórico do sistema (RLHF e posições)."""
import sqlite3
import os
from typing import List, Tuple
from barsi.domain.quant_ai import StatisticalBarsiEngine


def extract_training_data(db_path: str, min_examples: int = 3) -> Tuple[List[List[float]], List[float]]:
    """Extrai features e labels (0.0 / 1.0) a partir dos feedbacks do usuário registrados no SQLite."""
    if not os.path.exists(db_path):
        raise ValueError(f"Banco de dados não encontrado: {db_path}")

    X: List[List[float]] = []
    y: List[float] = []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Último feedback por ticker registrado em historico_analises
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
        """).fetchall()

        for row in rows:
            feedback = (row["feedback_usuario"] or "").upper()
            if "CONCORDO" in feedback:
                label = 1.0
            elif "DISCORDO" in feedback:
                label = 0.0
            else:
                continue

            preco = float(row["preco"] or 0.0)
            margem = float(row["margem_seguranca"] or 0.0)
            setor = (row["setor"] or "OUTROS").upper()
            div_proj = float(row["div_proj"] or 0.0) if row["div_proj"] else (preco * 0.06 if preco > 0 else 1.0)

            perenidade = StatisticalBarsiEngine.SETOR_STATS.get(
                setor,
                StatisticalBarsiEngine.SETOR_STATS.get("OUTROS", {"perenidade": 0.70})
            ).get("perenidade", 0.85)

            features = [
                0.60,  # Payout ratio médio padrão
                min(div_proj / max(preco, 0.01), 0.15) / 0.15,
                0.33,  # Debt/EBITDA normalizado
                0.50,  # ROE normalizado
                perenidade,
                min(max(margem / 100.0, -0.5), 1.0),
                0.50,  # Anos de pagamento
                0.77,  # Volatilidade inversa média
            ]
            X.append(features)
            y.append(label)
    finally:
        conn.close()

    if len(X) < min_examples:
        raise ValueError(
            f"Dados insuficientes: {len(X)} exemplos coletados (mínimo necessário: {min_examples}). "
            f"Envie feedbacks de concordo/discordo na interface /insights para calibrar a rede."
        )

    return X, y
