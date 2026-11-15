"""Wrapper de compatibilidade para treinamento offline do Payout Sustainability v2 (Sprint ML-2).

Prefira utilizar o CLI oficial: python -m barsi ml-retrain-payout
"""
from __future__ import annotations

import argparse
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "barsi_quant_ia", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from barsi.config import settings
from barsi.training.payout_trainer import train_and_export_payout_model

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="[Compatibilidade] Treinamento de Sustentabilidade de Dividendos (use `python -m barsi ml-retrain-payout`)"
    )
    parser.add_argument("pos_output", nargs="?", default=None, help="Caminho posicional de saída .json")
    parser.add_argument("--output", "-o", default=None, help="Caminho de saída .json de pesos")
    parser.add_argument("--db", default=settings.sqlite_db_path, help="Caminho para o SQLite com dados RLHF")
    parser.add_argument("--c-param", "-c", type=float, default=1.0, help="Fator de regularização inversa L2")
    args = parser.parse_args()

    target_output = args.output or args.pos_output or "memory/payout_weights.json"
    res = train_and_export_payout_model(
        db_path=args.db,
        output_path=target_output,
        c_param=args.c_param,
    )
    print(res)
