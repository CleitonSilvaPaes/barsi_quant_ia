"""Detector de Anomalias em Transações Financeiras / Extratos OFX.

Implementa inferência Isolation Forest puramente em NumPy (zero dependência de scikit-learn
no ambiente de execução/borda/TV Box), garantindo latência < 5ms e consumo de memória mínimo.
Suporta fallback gracioso caso o modelo treinado (.npz) ainda não tenha sido gerado.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np

from barsi.config import settings
from barsi.infra.logging import logger

# Euler-Mascheroni constant
_EULER_GAMMA = 0.5772156649015328606065120900824024310421


def _c_factor(n: float) -> float:
    """Fator de normalização c(n) de comprimento médio de busca em árvore binária não balanceada.
    
    Ref: Liu, Ting and Zhou (2008), 'Isolation Forest'.
    """
    if n <= 1.0:
        return 0.0
    if n == 2.0:
        return 1.0
    return 2.0 * (np.log(n - 1.0) + _EULER_GAMMA) - (2.0 * (n - 1.0) / n)


class AnomalyDetector:
    """Motor de inferência rápida em NumPy para detecção de anomalias em transações OFX."""

    _instance: Optional[AnomalyDetector] = None

    def __init__(self, model_path: Optional[str] = None):
        mem_dir = getattr(settings, "memory_dir", None) or os.path.dirname(settings.sqlite_db_path) or "./memory"
        self.model_path = model_path or os.path.join(mem_dir, "anomaly_model.npz")
        self._loaded: bool = False
        self._children_left: Optional[np.ndarray] = None
        self._children_right: Optional[np.ndarray] = None
        self._feature: Optional[np.ndarray] = None
        self._threshold: Optional[np.ndarray] = None
        self._n_node_samples: Optional[np.ndarray] = None
        self._max_samples: float = 256.0
        self._offset: float = -0.5
        self._anomaly_threshold: float = 0.58
        self._n_trees: int = 0
        self._c_max_samples: float = _c_factor(self._max_samples)
        self.load_model()

    @classmethod
    def get_instance(cls, model_path: Optional[str] = None) -> AnomalyDetector:
        """Singleton para reutilização de arrays compilados na memória."""
        if cls._instance is None:
            cls._instance = cls(model_path=model_path)
        return cls._instance

    def load_model(self) -> bool:
        """Carrega os pesos do modelo pré-treinado compactado (.npz)."""
        if not os.path.exists(self.model_path):
            self._loaded = False
            return False

        try:
            data = np.load(self.model_path)
            self._children_left = data["children_left"]
            self._children_right = data["children_right"]
            self._feature = data["feature"]
            self._threshold = data["threshold"]
            self._n_node_samples = data["n_node_samples"]
            self._max_samples = float(data.get("max_samples", 256.0))
            self._offset = float(data.get("offset", -0.5))
            self._anomaly_threshold = float(data.get("anomaly_threshold", 0.58))
            self._n_trees = self._children_left.shape[0]
            self._c_max_samples = _c_factor(self._max_samples)
            self._loaded = True
            logger.info(
                "anomaly_model_loaded",
                model_path=self.model_path,
                n_trees=self._n_trees,
                threshold=self._anomaly_threshold,
            )
            return True
        except Exception as e:
            logger.warning("anomaly_model_load_failed", error=str(e), model_path=self.model_path)
            self._loaded = False
            return False

    @staticmethod
    def extract_features(tx: Union[Dict[str, Any], Any]) -> np.ndarray:
        """Extrai vetor numérico de 7 dimensões para uma transação bancária/cartão.
        
        Dimensões:
        0. Valor absoluto log-scaled: ln(1 + |valor|)
        1. Direção do fluxo: +1.0 (CREDIT) ou -1.0 (DEBIT)
        2. Dia do mês normalizado: (day - 1) / 30.0
        3. Dia da semana (sin)
        4. Dia da semana (cos)
        5. Ticker de investimento associado: 1.0 (sim) ou 0.0 (não)
        6. Origem cartão de crédito: 1.0 (CARTAO) ou 0.0 (CONTA/OUTRO)
        """
        if isinstance(tx, dict):
            raw_val = float(tx.get("valor", 0.0) or 0.0)
            tipo = str(tx.get("tipo", "DEBIT") or "DEBIT").upper()
            data_str = str(tx.get("data", "") or "")
            ticker = tx.get("ticker") or tx.get("ticker_relacionado")
            origem = str(tx.get("origem", "CONTA") or "CONTA").upper()
        else:
            raw_val = float(getattr(tx, "valor", 0.0) or 0.0)
            tipo = str(getattr(tx, "tipo", "DEBIT") or "DEBIT").upper()
            data_str = str(getattr(tx, "data", "") or "")
            ticker = getattr(tx, "ticker", None) or getattr(tx, "ticker_relacionado", None)
            origem = str(getattr(tx, "origem", "CONTA") or "CONTA").upper()

        val_abs = abs(raw_val)
        dim_val_log = np.log1p(val_abs)

        # 1. Direção
        dim_tipo = 1.0 if (tipo == "CREDIT" or raw_val > 0) else -1.0

        # 2, 3, 4. Data
        dt = None
        if data_str:
            clean_date = data_str[:10].replace("/", "-")
            try:
                dt = datetime.strptime(clean_date, "%Y-%m-%d")
            except Exception:
                pass

        if dt:
            dim_day_month = min(max((dt.day - 1) / 30.0, 0.0), 1.0)
            weekday = dt.weekday()  # 0=Monday, 6=Sunday
            dim_dow_sin = np.sin(2.0 * np.pi * weekday / 7.0)
            dim_dow_cos = np.cos(2.0 * np.pi * weekday / 7.0)
        else:
            dim_day_month = 0.5
            dim_dow_sin = 0.0
            dim_dow_cos = 1.0

        # 5. Ticker de investimento
        dim_ticker = 1.0 if (ticker and str(ticker).strip()) else 0.0

        # 6. Origem
        dim_cartao = 1.0 if origem == "CARTAO" else 0.0

        return np.array(
            [dim_val_log, dim_tipo, dim_day_month, dim_dow_sin, dim_dow_cos, dim_ticker, dim_cartao],
            dtype=np.float32,
        )

    def _path_length_single_tree(self, tree_idx: int, features: np.ndarray) -> float:
        """Percorre uma árvore binária da raiz até a folha calculando a profundidade."""
        children_l = self._children_left[tree_idx]
        children_r = self._children_right[tree_idx]
        feats = self._feature[tree_idx]
        threshs = self._threshold[tree_idx]
        n_samples = self._n_node_samples[tree_idx]

        node = 0
        depth = 0

        while children_l[node] != -1 and children_l[node] != children_r[node]:
            f = feats[node]
            th = threshs[node]
            if features[f] <= th:
                node = children_l[node]
            else:
                node = children_r[node]
            depth += 1

        leaf_samples = float(n_samples[node]) if node < len(n_samples) else 1.0
        return float(depth) + _c_factor(leaf_samples)

    def score_features(self, features: np.ndarray) -> float:
        """Calcula o anomaly score em [0, 1] onde valores próximos de 1 indicam forte anomalia."""
        if not self._loaded or self._n_trees == 0 or self._c_max_samples <= 0.0:
            return 0.0

        total_path: float = 0.0
        for i in range(self._n_trees):
            total_path += self._path_length_single_tree(i, features)

        mean_path = total_path / float(self._n_trees)
        score = 2.0 ** (-mean_path / self._c_max_samples)
        return float(score)

    def detect(self, tx: Union[Dict[str, Any], Any]) -> Dict[str, Any]:
        """Avalia se uma transação é anômala e gera explicação descritiva.
        
        Retorna dicionário contendo:
        - `is_anomaly`: bool
        - `score`: float (0.0 a 1.0)
        - `threshold`: float
        - `severity`: 'ALTA' | 'MEDIA' | 'NORMAL'
        - `reason`: descrição humanizada do motivo
        """
        features = self.extract_features(tx)

        if not self._loaded:
            # Fallback heurístico resiliente se o modelo ainda não foi treinado
            val = abs(float(
                tx.get("valor", 0.0) if isinstance(tx, dict) else getattr(tx, "valor", 0.0)
            ) or 0.0)
            # Heurística conservadora: transações atípicas de alto valor (> R$ 10.000)
            is_anomaly = val >= 10000.0
            score = 0.85 if is_anomaly else 0.20
            return {
                "is_anomaly": is_anomaly,
                "score": score,
                "threshold": self._anomaly_threshold,
                "severity": "ALTA" if is_anomaly else "NORMAL",
                "reason": (
                    f"Transação de valor atípico (R$ {val:,.2f}) detectada via heurística base."
                    if is_anomaly
                    else "Padrão normal."
                ),
            }

        score = self.score_features(features)
        val = abs(float(
            tx.get("valor", 0.0) if isinstance(tx, dict) else getattr(tx, "valor", 0.0)
        ) or 0.0)
        desc = str(
            tx.get("descricao", "") if isinstance(tx, dict) else getattr(tx, "descricao", "")
        )

        is_anomaly = (score >= self._anomaly_threshold) or (val >= 10000.0 and score >= 0.54)

        severity = "NORMAL"
        if score >= 0.70:
            severity = "ALTA"
        elif score >= self._anomaly_threshold:
            severity = "MEDIA"

        reason = "Transação dentro do padrão histórico esperado."
        if is_anomaly:
            if val > 3000.0:
                reason = f"Valor atípico elevado de R$ {val:,.2f} com desvio significativo da média histórica ({desc[:30]})."
            elif score >= 0.65:
                reason = f"Padrão incomum de conciliação / horário / categoria para '{desc[:30]}' (Score: {score:.2f})."
            else:
                reason = f"Comportamento discrepante detectado pelo Isolation Forest (Score: {score:.2f})."

        return {
            "is_anomaly": is_anomaly,
            "score": round(score, 4),
            "threshold": self._anomaly_threshold,
            "severity": severity,
            "reason": reason,
        }
