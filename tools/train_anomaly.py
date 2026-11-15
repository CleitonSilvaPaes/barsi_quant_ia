"""Wrapper de compatibilidade — a lógica vive em `barsi.training.anomaly_trainer`.

Prefira: python -m barsi ml-retrain-anomaly
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "barsi_quant_ia", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from barsi.config import settings
from barsi.domain.anomaly_detector import _default_model_path
from barsi.training.anomaly_trainer import train_and_export_model

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else _default_model_path()
    print(train_and_export_model(db_path=settings.sqlite_db_path, output_path=out))
