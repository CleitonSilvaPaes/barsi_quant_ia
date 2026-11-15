"""Detector de Anomalias em Transações Financeiras / Extratos OFX.

Implementa inferência Isolation Forest puramente em NumPy (zero dependência de scikit-learn
no ambiente de execução/borda/TV Box), garantindo latência < 5ms e consumo de memória mínimo.
Suporta fallback gracioso caso o modelo treinado (.npz) ainda não tenha sido gerado.

Estrutura esperada do .npz (gerado por `barsi.training.anomaly_trainer`):
  - children_left:     (n_trees, max_nodes) int32   — filho esquerdo (-1 = folha)
  - children_right:    (n_trees, max_nodes) int32   — filho direito (-1 = folha)
  - feature:           (n_trees, max_nodes) int32   — feature de split (-2 = folha)
  - threshold:         (n_trees, max_nodes) float32 — limiar de split
  - n_node_samples:    (n_trees, max_nodes) int32   — amostras por nó
  - max_samples:       float — tamanho da subamostra (usado em c(n))
  - offset:            float — offset do sklearn (preservado, não usado na inferência)
  - anomaly_threshold: float — limiar calibrado pela contaminação
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, Optional

import numpy as np

from barsi.config import settings
from barsi.infra.logging import logger

# Constante de Euler-Mascheroni
_EULER_GAMMA = 0.5772156649015328606065120900824024310421


def _c_factor(n: float) -> float:
    """Fator de normalização c(n): comprimento médio de busca malsucedida em BST.

    Ref: Liu, Ting and Zhou (2008), 'Isolation Forest'.
    """
    if n <= 1.0:
        return 0.0
    if n == 2.0:
        return 1.0
    return 2.0 * (np.log(n - 1.0) + _EULER_GAMMA) - (2.0 * (n - 1.0) / n)


def _default_model_path() -> str:
    mem_dir = getattr(settings, "memory_dir", None) or os.path.dirname(settings.sqlite_db_path) or "./memory"
    return os.path.join(mem_dir, "anomaly_model.npz")


def to_tx_dict(tx: Any) -> Dict[str, Any]:
    """Normaliza dict / sqlite3.Row / pydantic (v1/v2) / objeto simples em dict."""
    if isinstance(tx, dict):
        return tx
    if isinstance(tx, sqlite3.Row):
        return dict(tx)
    if hasattr(tx, "model_dump"):
        return tx.model_dump()
    if hasattr(tx, "dict") and callable(tx.dict):
        return tx.dict()
    return dict(vars(tx))


class AnomalyDetector:
    """Motor de inferência rápida em NumPy para detecção de anomalias em transações OFX."""

    _instance: Optional[AnomalyDetector] = None

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path or _default_model_path()
        self._loaded: bool = False
        self._children_left: Optional[np.ndarray] = None
        self._children_right: Optional[np.ndarray] = None
        self._feature: Optional[np.ndarray] = None
        self._threshold: Optional[np.ndarray] = None
        self._n_node_samples: Optional[np.ndarray] = None
        self._max_samples: float = 256.0
        self._offset: float = -0.5
        self._anomaly_threshold: float = settings.anomaly_default_threshold
        self._n_trees: int = 0
        self._c_max_samples: float = _c_factor(self._max_samples)
        self.load_model()

    # ----------------- Singleton -----------------

    @classmethod
    def get_instance(cls, model_path: Optional[str] = None) -> AnomalyDetector:
        """Singleton reutilizável; recria a instância se outro `model_path` for solicitado."""
        if cls._instance is None:
            cls._instance = cls(model_path=model_path)
        elif model_path and cls._instance.model_path != model_path:
            cls._instance = cls(model_path=model_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Descarta o singleton (útil em testes e após retreinar o modelo)."""
        cls._instance = None

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def anomaly_threshold(self) -> float:
        return self._anomaly_threshold

    # ----------------- Carga do modelo -----------------

    def load_model(self) -> bool:
        """Carrega os pesos do modelo pré-treinado compactado (.npz)."""
        if not os.path.exists(self.model_path):
            self._loaded = False
            logger.warning(
                "anomaly_model_missing_fallback",
                model_path=self.model_path,
                hint="Execute `python -m barsi ml-retrain-anomaly` para treinar o modelo. "
                     "Até lá, apenas a heurística de valor alto é aplicada e notificações ficam suspensas.",
            )
            return False

        try:
            with np.load(self.model_path) as data:
                self._children_left = data["children_left"]
                self._children_right = data["children_right"]
                self._feature = data["feature"]
                self._threshold = data["threshold"]
                self._n_node_samples = data["n_node_samples"]
                self._max_samples = float(data["max_samples"]) if "max_samples" in data else 256.0
                self._offset = float(data["offset"]) if "offset" in data else -0.5
                self._anomaly_threshold = (
                    float(data["anomaly_threshold"]) if "anomaly_threshold" in data
                    else settings.anomaly_default_threshold
                )
            self._n_trees = int(self._children_left.shape[0])
            self._c_max_samples = _c_factor(self._max_samples)
            self._loaded = True
            logger.info(
                "anomaly_model_loaded",
                model_path=self.model_path,
                n_trees=self._n_trees,
                threshold=self._anomaly_threshold,
            )
            return True
        except Exception as e:  # noqa: BLE001 — .npz corrompido nunca derruba a API
            logger.warning("anomaly_model_load_failed", error=str(e), model_path=self.model_path)
            self._loaded = False
            return False

    # ----------------- Features -----------------

    @staticmethod
    def extract_features(tx: Any) -> np.ndarray:
        """Extrai vetor numérico de 7 dimensões para uma transação bancária/cartão.

        0. ln(1 + |valor|)
        1. Direção: +1.0 (CREDIT) / -1.0 (DEBIT)
        2. Dia do mês normalizado: (day - 1) / 30
        3. Dia da semana (sin)
        4. Dia da semana (cos)
        5. Ticker de investimento associado (1/0)
        6. Origem cartão de crédito (1/0)
        """
        d = to_tx_dict(tx)
        raw_val = float(d.get("valor", 0.0) or 0.0)
        tipo = str(d.get("tipo", "DEBIT") or "DEBIT").upper()
        data_str = str(d.get("data", "") or "")
        ticker = d.get("ticker") or d.get("ticker_relacionado")
        origem = str(d.get("origem", "CONTA") or "CONTA").upper()

        dim_val_log = np.log1p(abs(raw_val))
        dim_tipo = 1.0 if (tipo == "CREDIT" or raw_val > 0) else -1.0

        dt = None
        if data_str:
            try:
                dt = datetime.strptime(data_str[:10].replace("/", "-"), "%Y-%m-%d")
            except ValueError:
                dt = None

        if dt:
            dim_day_month = min(max((dt.day - 1) / 30.0, 0.0), 1.0)
            weekday = dt.weekday()
            dim_dow_sin = np.sin(2.0 * np.pi * weekday / 7.0)
            dim_dow_cos = np.cos(2.0 * np.pi * weekday / 7.0)
        else:
            dim_day_month, dim_dow_sin, dim_dow_cos = 0.5, 0.0, 1.0

        dim_ticker = 1.0 if (ticker and str(ticker).strip()) else 0.0
        dim_cartao = 1.0 if origem == "CARTAO" else 0.0

        return np.array(
            [dim_val_log, dim_tipo, dim_day_month, dim_dow_sin, dim_dow_cos, dim_ticker, dim_cartao],
            dtype=np.float32,
        )

    # ----------------- Inferência -----------------

    def _path_length_single_tree(self, tree_idx: int, features: np.ndarray) -> float:
        """Percorre uma árvore da raiz até a folha (folha sklearn: children_left == -1)."""
        children_l = self._children_left[tree_idx]
        children_r = self._children_right[tree_idx]
        feats = self._feature[tree_idx]
        threshs = self._threshold[tree_idx]
        n_samples = self._n_node_samples[tree_idx]

        node = 0
        depth = 0
        while children_l[node] != -1:
            if features[feats[node]] <= threshs[node]:
                node = children_l[node]
            else:
                node = children_r[node]
            depth += 1

        return float(depth) + _c_factor(float(n_samples[node]))

    def score_features(self, features: np.ndarray) -> float:
        """Anomaly score em [0, 1]; valores próximos de 1 indicam forte anomalia."""
        if not self._loaded or self._n_trees == 0 or self._c_max_samples <= 0.0:
            return 0.0
        total_path = sum(self._path_length_single_tree(i, features) for i in range(self._n_trees))
        mean_path = total_path / float(self._n_trees)
        return float(2.0 ** (-mean_path / self._c_max_samples))

    def detect(self, tx: Any) -> Dict[str, Any]:
        """Avalia se uma transação é anômala.

        Retorna: is_anomaly, score, threshold, severity ('ALTA'|'MEDIA'|'NORMAL'),
        reason e `fallback` (True quando o modelo .npz não está carregado).
        """
        d = to_tx_dict(tx)
        val = abs(float(d.get("valor", 0.0) or 0.0))
        desc = str(d.get("descricao", "") or "")
        high_value = val >= settings.anomaly_high_value_amount

        if not self._loaded:
            return {
                "is_anomaly": high_value,
                "score": 0.85 if high_value else 0.20,
                "threshold": self._anomaly_threshold,
                "severity": "ALTA" if high_value else "NORMAL",
                "reason": (
                    f"Transação de valor atípico (R$ {val:,.2f}) detectada via heurística base (modelo não treinado)."
                    if high_value else "Padrão normal."
                ),
                "fallback": True,
            }

        score = self.score_features(self.extract_features(d))
        is_anomaly = (score >= self._anomaly_threshold) or (
            high_value and score >= settings.anomaly_high_value_score
        )

        if score >= settings.anomaly_severity_alta:
            severity = "ALTA"
        elif is_anomaly:
            severity = "MEDIA"
        else:
            severity = "NORMAL"

        reason = "Transação dentro do padrão histórico esperado."
        if is_anomaly:
            if val > settings.anomaly_explain_value_threshold:
                reason = f"Valor atípico elevado de R$ {val:,.2f} com desvio significativo da média histórica ({desc[:30]})."
            elif score >= settings.anomaly_explain_pattern_score:
                reason = f"Padrão incomum de data / origem / categoria para '{desc[:30]}' (Score: {score:.2f})."
            else:
                reason = f"Comportamento discrepante detectado pelo Isolation Forest (Score: {score:.2f})."

        return {
            "is_anomaly": is_anomaly,
            "score": round(score, 4),
            "threshold": self._anomaly_threshold,
            "severity": severity,
            "reason": reason,
            "fallback": False,
        }
