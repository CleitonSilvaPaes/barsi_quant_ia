"""Wrapper de compatibilidade — a lógica vive em `barsi.training.anomaly_trainer`.

Prefira utilizar o CLI oficial: python -m barsi ml-retrain-anomaly
"""

from __future__ import annotations

import argparse
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "barsi_quant_ia", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from barsi.config import settings
from barsi.domain.anomaly_detector import _default_model_path
from barsi.training.anomaly_trainer import train_and_export_model

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="[Compatibilidade] Treinamento offline de Isolation Forest (use `python -m barsi ml-retrain-anomaly`)"
    )
    parser.add_argument("pos_output", nargs="?", default=None, help="Caminho posicional de saída .npz")
    parser.add_argument("--output", "-o", default=None, help="Caminho de saída .npz")
    parser.add_argument("--db", default=settings.sqlite_db_path, help="Caminho para o SQLite")
    parser.add_argument("--contamination", type=float, default=None, help="Taxa estimada de contaminação")
    parser.add_argument("--trees", type=int, default=None, help="Número de árvores na floresta")
    args = parser.parse_args()

    target_output = args.output or args.pos_output or _default_model_path()
    res = train_and_export_model(
        db_path=args.db,
        output_path=target_output,
        contamination=args.contamination,
        n_estimators=args.trees,
    )
    print(res)
