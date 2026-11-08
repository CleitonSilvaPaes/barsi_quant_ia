"""TinyMLP — Multi-Layer Perceptron mínimo para Edge (TV Box ARM64).

Zero dependências externas além de math/json.
- 1 camada oculta (tanh)
- Saída sigmoide
- ~60 parâmetros, ~250 bytes serializados
- Inferência < 10 μs
"""
import math
import json
import os
from typing import List, Optional


class TinyMLP:
    """Multi-Layer Perceptron puro em Python com 1 camada oculta e saída sigmoide."""

    def __init__(self, n_inputs: int = 8, n_hidden: int = 6, weights_path: Optional[str] = None):
        self.n_inputs = n_inputs
        self.n_hidden = n_hidden
        self.weights_path = weights_path

        # Inicialização com zeros até carregamento de pesos
        self.W1 = [[0.0] * n_hidden for _ in range(n_inputs)]
        self.b1 = [0.0] * n_hidden
        self.W2 = [0.0] * n_hidden
        self.b2 = 0.0

        if weights_path and os.path.exists(weights_path):
            self.load(weights_path)

    @staticmethod
    def _sigmoid(x: float) -> float:
        """Função sigmoide numericamente estável para evitar overflow."""
        if x >= 0:
            return 1.0 / (1.0 + math.exp(-x))
        ex = math.exp(x)
        return ex / (1.0 + ex)

    def forward(self, x: List[float]) -> float:
        """Forward pass — retorna probabilidade estimada em [0, 1]."""
        if len(x) != self.n_inputs:
            raise ValueError(f"Esperado {self.n_inputs} inputs, recebido {len(x)}")

        # Camada oculta (ativação tanh)
        hidden = [0.0] * self.n_hidden
        for j in range(self.n_hidden):
            z = self.b1[j]
            for i in range(self.n_inputs):
                z += self.W1[i][j] * x[i]
            hidden[j] = math.tanh(z)

        # Camada de saída (ativação sigmoide)
        z_out = self.b2
        for j in range(self.n_hidden):
            z_out += self.W2[j] * hidden[j]
        return self._sigmoid(z_out)

    def save(self, path: Optional[str] = None) -> None:
        """Serializa os parâmetros aprendidos em formato JSON leve."""
        target_path = path or self.weights_path
        if not target_path:
            raise ValueError("Nenhum path especificado para save()")
        dir_name = os.path.dirname(target_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump({
                "n_inputs": self.n_inputs,
                "n_hidden": self.n_hidden,
                "W1": self.W1,
                "b1": self.b1,
                "W2": self.W2,
                "b2": self.b2,
            }, f, indent=2)

    def load(self, path: str) -> None:
        """Carrega os pesos a partir de um arquivo JSON serializado."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.n_inputs = data["n_inputs"]
        self.n_hidden = data["n_hidden"]
        self.W1 = data["W1"]
        self.b1 = data["b1"]
        self.W2 = data["W2"]
        self.b2 = data["b2"]
