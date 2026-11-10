"""Coleta dados de treinamento do próprio histórico do sistema (RLHF e posições)."""
import sqlite3
import os
from typing import List, Tuple
from barsi.domain.quant_ai import StatisticalBarsiEngine

# Constantes de calibração baseadas nas médias históricas dos setores B.E.S.T. da B3
DEFAULT_PAYOUT_RATIO = 0.60       # Payout ratio médio sustentável (~60%)
DEFAULT_DEBT_EBITDA_INV = 0.33    # Dívida Líquida/EBITDA normalizada (~2.0x)
DEFAULT_ROE_NORMALIZED = 0.50     # Retorno sobre patrimônio líquido (~15% a.a.)
DEFAULT_YEARS_PAYING = 0.50       # Histórico de pagamento consecutivo (meta 5 anos = 0.5)
DEFAULT_VOL_INVERSE = 0.77        # Volatilidade inversa média de ações perenes



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
                DEFAULT_PAYOUT_RATIO,
                min(div_proj / max(preco, 0.01), 0.15) / 0.15,
                DEFAULT_DEBT_EBITDA_INV,
                DEFAULT_ROE_NORMALIZED,
                perenidade,
                min(max(margem / 100.0, -0.5), 1.0),
                DEFAULT_YEARS_PAYING,
                DEFAULT_VOL_INVERSE,
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

