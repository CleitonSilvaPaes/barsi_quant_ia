"""Treina o Dividends Sustainability Score (DSS).

Executado no PC do desenvolvedor ou sob demanda para calibração.
Gera pesos compactos (~250 bytes) que rodam com latência zero na TV Box ARM64.
"""
import math
import random
from typing import List, Tuple
from barsi.domain.tiny_mlp import TinyMLP


def train(
    X: List[List[float]],
    y: List[float],
    n_hidden: int = 6,
    epochs: int = 3000,
    lr: float = 0.08,
    seed: int = 42,
) -> TinyMLP:
    """Gradient descent com log-loss e backpropagation analítica pura."""
    random.seed(seed)
    n = len(X)
    n_in = len(X[0])
    model = TinyMLP(n_inputs=n_in, n_hidden=n_hidden)

    # Inicialização Xavier com ruído gaussiano
    model.W1 = [[random.gauss(0, 0.3) for _ in range(n_hidden)] for _ in range(n_in)]
    model.b1 = [random.gauss(0, 0.1) for _ in range(n_hidden)]
    model.W2 = [random.gauss(0, 0.3) for _ in range(n_hidden)]
    model.b2 = random.gauss(0, 0.1)

    for epoch in range(epochs):
        total_loss = 0.0
        for xi, yi in zip(X, y):
            # 1. Forward pass
            hidden = [
                math.tanh(model.b1[j] + sum(model.W1[i][j] * xi[i] for i in range(n_in)))
                for j in range(n_hidden)
            ]
            z_out = model.b2 + sum(model.W2[j] * hidden[j] for j in range(n_hidden))
            pred = (
                1.0 / (1.0 + math.exp(-z_out))
                if z_out >= 0
                else math.exp(z_out) / (1.0 + math.exp(z_out))
            )

            # Log-loss clipping para estabilidade numérica
            pred_c = max(min(pred, 1.0 - 1e-7), 1e-7)
            total_loss += -(yi * math.log(pred_c) + (1.0 - yi) * math.log(1.0 - pred_c))

            # 2. Backpropagation (dLoss/dz = pred - y)
            err = pred - yi
            for j in range(n_hidden):
                grad_h = err * model.W2[j] * (1.0 - hidden[j] ** 2)
                model.W2[j] -= lr * err * hidden[j]
                for i in range(n_in):
                    model.W1[i][j] -= lr * grad_h * xi[i]
                model.b1[j] -= lr * grad_h
            model.b2 -= lr * err

    return model


# Dataset curado com base na tese Barsi B.E.S.T. vs empresas cíclicas/alto risco
# Features: [payout, yield, debt_inv, roe, perenidade, margem, hist_pgto, vol_inv]
TRAINING_DATA: List[Tuple[List[float], float]] = [
    # Exemplos de alta sustentabilidade (label = 1.0)
    ([0.65, 0.85, 0.72, 0.75, 0.95, 0.60, 1.00, 0.55], 1.0),  # Bancos sólidos (BBAS3 / ITUB4)
    ([0.80, 0.90, 0.65, 0.70, 0.98, 0.45, 0.90, 0.60], 1.0),  # Transmissão de Energia (TAEE11 / TRPL4)
    ([0.70, 0.75, 0.80, 0.68, 0.99, 0.55, 0.80, 0.65], 1.0),  # Saneamento Básico (SAPR4 / CSMG3)
    ([0.60, 0.70, 0.85, 0.80, 0.95, 0.40, 0.70, 0.70], 1.0),  # Seguradoras (BBSE3 / CXSE3)
    ([0.85, 0.88, 0.75, 0.72, 0.85, 0.35, 0.60, 0.50], 1.0),  # FIIs perenes de papel e tijolo (MXRF11)
    # Exemplos de baixa sustentabilidade / histórico de cortes (label = 0.0)
    ([-0.30, 0.20, 0.30, 0.15, 0.70, -0.20, 0.30, 0.35], 0.0),  # Telecomunicação em crise (OIBR3)
    ([0.40, 0.15, 0.25, 0.05, 0.70, -0.15, 0.40, 0.30], 0.0),   # Reestruturação / corte (IRBR3)
    ([0.95, 0.45, 0.40, 0.35, 0.70, 0.10, 0.20, 0.40], 0.0),    # Varejo / turismo cíclico (CVCB3)
    ([0.98, 0.30, 0.20, 0.10, 0.60, -0.40, 0.10, 0.25], 0.0),   # Cíclica com dívida extrema
]


def train_and_export_weights(output_path: str = "memory/dss_weights.json") -> TinyMLP:
    """Executa o treinamento e salva os pesos no arquivo destino."""
    X = [row[0] for row in TRAINING_DATA]
    y = [row[1] for row in TRAINING_DATA]
    model = train(X, y, epochs=3000, lr=0.08)
    model.save(output_path)
    return model


if __name__ == "__main__":
    trained_model = train_and_export_weights("memory/dss_weights.json")
    print("✅ Modelo DSS treinado e exportado para memory/dss_weights.json")
