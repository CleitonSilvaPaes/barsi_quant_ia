"""Treina o Dividends Sustainability Score (DSS).

Executado no PC do desenvolvedor ou sob demanda para auto-aprimoramento contínuo.
Gera pesos compactos (~250 bytes) que rodam com latência zero na TV Box ARM64.
"""
import os
import math
import random
from typing import List, Tuple, Dict, Any, Optional
from barsi.domain.tiny_mlp import TinyMLP
from barsi.training.dataset import extract_training_data
from barsi.infra.logging import logger

# Dataset BASE (curated seed — sempre incluído)
# Features: [payout, yield, debt_inv, roe, perenidade, margem, hist_pgto, vol_inv]
BASE_TRAINING_DATA: List[Tuple[List[float], float]] = [
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
    n_in = len(X[0])
    model = TinyMLP(n_inputs=n_in, n_hidden=n_hidden)

    # Inicialização Xavier com ruído gaussiano
    model.W1 = [[random.gauss(0, 0.3) for _ in range(n_hidden)] for _ in range(n_in)]
    model.b1 = [random.gauss(0, 0.1) for _ in range(n_hidden)]
    model.W2 = [random.gauss(0, 0.3) for _ in range(n_hidden)]
    model.b2 = random.gauss(0, 0.1)

    for _ in range(epochs):
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

            # 2. Backpropagation
            err = pred - yi
            for j in range(n_hidden):
                grad_h = err * model.W2[j] * (1.0 - hidden[j] ** 2)
                model.W2[j] -= lr * err * hidden[j]
                for i in range(n_in):
                    model.W1[i][j] -= lr * grad_h * xi[i]
                model.b1[j] -= lr * grad_h
            model.b2 -= lr * err

    return model


def train_dynamic(
    X: List[List[float]],
    y: List[float],
    n_hidden: int = 6,
    epochs: int = 2000,
    lr: float = 0.05,
    val_split: float = 0.2,
    seed: int = 42,
) -> Tuple[TinyMLP, Dict[str, Any]]:
    """Treinamento com validação holdout e early stopping para prevenção de overfitting."""
    random.seed(seed)
    n = len(X)
    if n < 5:
        raise ValueError(f"Exemplos insuficientes para treino e validação: {n}")

    indices = list(range(n))
    random.shuffle(indices)
    n_val = max(1, int(n * val_split))
    val_idx = indices[:n_val]
    train_idx = indices[n_val:]

    X_train = [X[i] for i in train_idx]
    y_train = [y[i] for i in train_idx]
    X_val = [X[i] for i in val_idx]
    y_val = [y[i] for i in val_idx]

    n_in = len(X[0])
    model = TinyMLP(n_inputs=n_in, n_hidden=n_hidden)

    model.W1 = [[random.gauss(0, 0.3) for _ in range(n_hidden)] for _ in range(n_in)]
    model.b1 = [random.gauss(0, 0.1) for _ in range(n_hidden)]
    model.W2 = [random.gauss(0, 0.3) for _ in range(n_hidden)]
    model.b2 = random.gauss(0, 0.1)

    best_val_loss = float("inf")
    best_weights = None
    patience = 400
    patience_counter = 0

    for _ in range(epochs):
        # 1. Passo de treino
        for xi, yi in zip(X_train, y_train):
            hidden = [
                math.tanh(model.b1[j] + sum(model.W1[i][j] * xi[i] for i in range(n_in)))
                for j in range(n_hidden)
            ]
            z_out = model.b2 + sum(model.W2[j] * hidden[j] for j in range(n_hidden))
            pred = 1.0 / (1.0 + math.exp(-z_out)) if z_out >= 0 else math.exp(z_out) / (1.0 + math.exp(z_out))
            err = pred - yi
            for j in range(n_hidden):
                grad_h = err * model.W2[j] * (1.0 - hidden[j] ** 2)
                model.W2[j] -= lr * err * hidden[j]
                for i in range(n_in):
                    model.W1[i][j] -= lr * grad_h * xi[i]
                model.b1[j] -= lr * grad_h
            model.b2 -= lr * err

        # 2. Passo de validação
        val_loss = 0.0
        val_correct = 0
        for xi, yi in zip(X_val, y_val):
            pred = model.forward(xi)
            pred_c = max(min(pred, 1.0 - 1e-7), 1e-7)
            val_loss += -(yi * math.log(pred_c) + (1.0 - yi) * math.log(1.0 - pred_c))
            if (pred >= 0.5) == (yi >= 0.5):
                val_correct += 1
        val_loss /= len(X_val)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_weights = ([row[:] for row in model.W1], model.b1[:], model.W2[:], model.b2)
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= patience:
                break

    if best_weights:
        model.W1, model.b1, model.W2, model.b2 = best_weights

    # Mede acurácia final nos pesos restaurados
    final_val_correct = sum(1 for xi, yi in zip(X_val, y_val) if (model.forward(xi) >= 0.5) == (yi >= 0.5))
    metrics = {
        "n_train": len(X_train),
        "n_val": len(X_val),
        "best_val_loss": round(best_val_loss, 4),
        "best_val_acc": round(final_val_correct / len(X_val), 3),
    }
    return model, metrics


def train_full_pipeline(
    db_path: Optional[str] = None,
    output_path: str = "memory/dss_weights.json",
    use_system_data: bool = True,
) -> Dict[str, Any]:
    """Pipeline completo de treinamento: extrai dados de RLHF, combina com o dataset base e promove modelo."""
    X_all = [row[0] for row in BASE_TRAINING_DATA]
    y_all = [row[1] for row in BASE_TRAINING_DATA]
    system_examples_count = 0

    if use_system_data and db_path and os.path.exists(db_path):
        try:
            X_sys, y_sys = extract_training_data(db_path)
            system_examples_count = len(X_sys)
            X_all.extend(X_sys)
            y_all.extend(y_sys)
            logger.info("dss_training_system_data_loaded", count=system_examples_count)
        except Exception as e:
            logger.warning("dss_training_system_data_skipped", reason=str(e))

    model, metrics = train_dynamic(X_all, y_all)
    metrics["total_examples"] = len(X_all)
    metrics["system_examples"] = system_examples_count

    # Backup do modelo anterior antes de sobrescrever
    if os.path.exists(output_path):
        backup_path = output_path.replace(".json", "_prev.json")
        try:
            os.replace(output_path, backup_path)
        except Exception:
            pass

    model.save(output_path)

    # Invalida singleton para recarregar pesos na próxima inferência
    from barsi.domain.quant_ai import DividendSustainabilityScorer
    DividendSustainabilityScorer.reload()

    return metrics
