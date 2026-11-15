"""Motor de inferência e pontuação de sustentabilidade de dividendos (Sprint ML-2).

Utiliza Regressão Logística compacta e explicável (pesos w e intercept b),
gerando probabilidades calibradas P(sustentável) em puro Python/NumPy (< 0.1ms na TV Box ARM64).
Suporta calibração via feedback do usuário (RLHF) e fallback robusto Luiz Barsi.
"""
import os
import json
import math
from typing import List, Dict, Any, Optional, Union
from barsi.domain.models import Position
from barsi.infra.logging import logger

PAYOUT_FEATURE_NAMES = [
    "payout_ratio",
    "dividend_yield",
    "debt_ebitda_inv",
    "roe",
    "perenidade_setor",
    "margem_seguranca",
    "anos_pagando",
    "volatilidade_inv",
]

# Pesos baseline calibrados nos princípios conservadores de Luiz Barsi Filho (fallback)
BASELINE_WEIGHTS = [1.8, 2.2, 1.9, 1.2, 2.5, 1.5, 1.6, 1.0]
BASELINE_INTERCEPT = -6.0


def _sigmoid(x: float) -> float:
    """Função sigmoide estável numericamente para evitar overflow."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


def extract_position_features(posicao: Position, extras: Optional[Dict[str, Any]] = None) -> List[float]:
    """Extrai e normaliza as 8 features de sustentabilidade a partir da Posição e indicadores extras."""
    extras = extras or {}
    div_proj = float(posicao.div_proj or 0.0)
    preco = max(float(posicao.preco_atual or 1.0), 0.01)
    margem = float(posicao.margem_seguranca or 0.0)
    lucro_estimado = max(float(extras.get("lucro_estimado", 1.0)), 0.01)

    # 1. Payout ratio normalizado
    f_payout = min(div_proj * 4.0 / lucro_estimado, 2.0) / 2.0
    # 2. Dividend Yield projetado normalizado (base 15% a.a.)
    f_dy = min(div_proj / preco, 0.15) / 0.15
    # 3. Inverso da Dívida Líquida / EBITDA (quanto menor a dívida, maior o score)
    f_debt_inv = 1.0 / (1.0 + max(float(extras.get("deb_ebitda", 2.0)), 0.0))
    # 4. ROE normalizado (base 30% a.a.)
    f_roe = min(max(float(extras.get("roe", 0.15)), 0.0), 0.30) / 0.30
    # 5. Perenidade do setor B.E.S.T. (0.70 a 1.00)
    f_perenidade = max(min(float(extras.get("perenidade", 0.85)), 1.0), 0.0)
    # 6. Margem de segurança Barsi (-50% a +100%)
    f_margem = min(max(margem / 100.0, -0.5), 1.0)
    # 7. Histórico consecutivo de dividendos (base 10 anos)
    f_anos = min(max(float(extras.get("anos_pagando", 5.0)), 0.0) / 10.0, 1.0)
    # 8. Inverso da volatilidade anualizada
    f_vol_inv = 1.0 / (1.0 + max(float(extras.get("vol_anual", 0.30)), 0.0))

    return [
        round(f_payout, 4),
        round(f_dy, 4),
        round(f_debt_inv, 4),
        round(f_roe, 4),
        round(f_perenidade, 4),
        round(f_margem, 4),
        round(f_anos, 4),
        round(f_vol_inv, 4),
    ]


class PayoutScorer:
    """Avaliador probabilístico e explicável de sustentabilidade de proventos via Regressão Logística."""

    _instance: Optional["PayoutScorer"] = None

    def __init__(self, weights_path: Optional[str] = None):
        self.weights_path = weights_path or self._resolve_default_path()
        self.feature_names = list(PAYOUT_FEATURE_NAMES)
        self.weights = list(BASELINE_WEIGHTS)
        self.intercept = float(BASELINE_INTERCEPT)
        self.threshold_alta = 0.70
        self.threshold_media = 0.40
        self.metrics: Dict[str, Any] = {}
        self._loaded = False
        self.load_weights()

    @classmethod
    def get_instance(cls, weights_path: Optional[str] = None) -> "PayoutScorer":
        if cls._instance is None or (weights_path and cls._instance.weights_path != weights_path):
            cls._instance = PayoutScorer(weights_path=weights_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Invalida a instância singleton forçando recarga dos pesos aprendidos."""
        cls._instance = None

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def feature_summary(self) -> Dict[str, Dict[str, Any]]:
        """Retorna o resumo padronizado de pesos e direção ('favoravel' / 'desfavoravel')."""
        return {
            name: {"weight": round(w, 4), "sinal": "favoravel" if w > 0 else "desfavoravel"}
            for name, w in zip(self.feature_names, self.weights)
        }

    @staticmethod
    def _resolve_default_path() -> str:
        candidates = [
            "memory/payout_weights.json",
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "memory", "payout_weights.json"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return "memory/payout_weights.json"

    def load_weights(self) -> bool:
        """Carrega os pesos calibrados a partir do arquivo JSON serializado."""
        if not self.weights_path or not os.path.exists(self.weights_path):
            self._loaded = False
            logger.info("payout_scorer_using_baseline_fallback", path=self.weights_path)
            return False

        try:
            with open(self.weights_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Suporte ao formato novo (Logistic Regression) e ao formato TinyMLP legado
            if "weights" in data and "intercept" in data:
                self.weights = [float(w) for w in data["weights"]]
                self.intercept = float(data["intercept"])
                if "feature_names" in data:
                    self.feature_names = list(data["feature_names"])
                if "thresholds" in data:
                    self.threshold_alta = float(data["thresholds"].get("alta", 0.70))
                    self.threshold_media = float(data["thresholds"].get("media", 0.40))
                self.metrics = data.get("metrics", {})
                self._loaded = True
                logger.info(
                    "payout_weights_loaded",
                    path=self.weights_path,
                    n_features=len(self.weights),
                    intercept=self.intercept,
                )
                return True
            elif "W1" in data and "b2" in data:
                # Formato TinyMLP legado: pesos do layer 2 como aproximação linear
                w1 = data.get("W1", [])
                w2 = data.get("W2", [])
                b2 = data.get("b2", 0.0)
                if w1 and w2:
                    n_in = len(w1)
                    n_h = len(w2)
                    approx_weights = []
                    for i in range(n_in):
                        approx_weights.append(sum(w1[i][j] * w2[j] for j in range(n_h)))
                    self.weights = approx_weights
                    self.intercept = float(b2)
                    self._loaded = True
                    return True
        except Exception as e:
            logger.warning("payout_weights_load_error_fallback", error=str(e), path=self.weights_path)

        self._loaded = False
        return False

    def score_features(self, features: Union[List[float], Dict[str, float]]) -> Dict[str, Any]:
        """Calcula o escore de probabilidade de sustentabilidade e a contribuição explicável de cada feature."""
        if isinstance(features, dict):
            feat_vec = [float(features.get(name, 0.5)) for name in self.feature_names]
        else:
            feat_vec = [float(x) for x in features]

        if len(feat_vec) != len(self.weights):
            raise ValueError(f"Esperado {len(self.weights)} features, recebido {len(feat_vec)}")

        logit = self.intercept
        contributions: Dict[str, float] = {}
        for name, w, x in zip(self.feature_names, self.weights, feat_vec):
            contrib = w * x
            logit += contrib
            contributions[name] = round(contrib, 3)

        prob = _sigmoid(logit)
        prob_rounded = round(prob, 3)

        if prob >= self.threshold_alta:
            label = "ALTA"
            badge = "🟢"
        elif prob >= self.threshold_media:
            label = "MÉDIA"
            badge = "🟡"
        else:
            label = "BAIXA"
            badge = "🔴"

        return {
            "score": prob_rounded,
            "label": label,
            "badge": badge,
            "logit": round(logit, 3),
            "intercept": round(self.intercept, 3),
            "contributions": contributions,
            "is_loaded": self._loaded,
            "weights_path": os.path.basename(self.weights_path) if self.weights_path else None,
            "model_version": "v2_logistic_regression",
        }

    def score(self, posicao: Position, extras: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Avalia a posição financeira calculando sustentabilidade com fallback e explicabilidade."""
        div_proj = float(posicao.div_proj or 0.0)
        if div_proj <= 0.0:
            return {
                "score": 0.0,
                "label": "BAIXA",
                "badge": "🔴",
                "logit": -99.0,
                "intercept": self.intercept,
                "contributions": {},
                "is_loaded": self._loaded,
                "model_version": "v2_logistic_regression",
            }

        features = extract_position_features(posicao, extras)
        return self.score_features(features)
