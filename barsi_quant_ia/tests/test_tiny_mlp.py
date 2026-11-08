"""Testes unitários para o TinyMLP e DividendSustainabilityScorer."""
import os
import math
import pytest
from barsi.domain.tiny_mlp import TinyMLP
from barsi.domain.quant_ai import DividendSustainabilityScorer
from barsi.domain.models import Position


def test_mlp_forward_shape():
    """Forward pass retorna valor em [0, 1]."""
    model = TinyMLP(n_inputs=8, n_hidden=6)
    pred = model.forward([0.5] * 8)
    assert 0.0 <= pred <= 1.0


def test_mlp_save_load_roundtrip(tmp_path):
    """Persistência de pesos JSON preserva o comportamento exato."""
    model = TinyMLP(n_inputs=8, n_hidden=6)
    model.W1 = [[0.1 * (i + j) for j in range(6)] for i in range(8)]
    model.b1 = [0.05 * j for j in range(6)]
    model.W2 = [0.2 * j for j in range(6)]
    model.b2 = 0.1

    x = [0.5] * 8
    pred1 = model.forward(x)

    path = str(tmp_path / "weights.json")
    model.save(path)

    model2 = TinyMLP(weights_path=path)
    pred2 = model2.forward(x)

    assert abs(pred1 - pred2) < 1e-10


def test_mlp_rejects_wrong_input_count():
    """Lança ValueError descritivo quando o número de inputs é incorreto."""
    model = TinyMLP(n_inputs=8)
    with pytest.raises(ValueError, match="Esperado 8 inputs"):
        model.forward([0.1, 0.2])


def test_sigmoid_numerical_stability():
    """Sigmoide com proteção numérica para valores extremos."""
    model = TinyMLP(n_inputs=1, n_hidden=1)
    model.W2 = [1.0]
    model.b2 = 1000.0  # valor gigante positivo
    assert model.forward([0.0]) == pytest.approx(1.0, abs=1e-6)

    model.b2 = -1000.0  # valor gigante negativo
    assert model.forward([0.0]) == pytest.approx(0.0, abs=1e-6)


def test_dividend_sustainability_scorer(tmp_path):
    """Valida o cálculo do score DSS e a categorização por badge."""
    p = Position(
        ticker="BBAS3",
        row_index=10,
        preco_atual=27.50,
        div_proj=2.85,
        preco_teto=47.50,
        margem_seguranca=72.7,
        quantidade=100
    )
    scorer = DividendSustainabilityScorer.get()
    res = scorer.score(p, extras={"perenidade": 0.95, "roe": 0.22, "anos_pagando": 10})

    assert "score" in res
    assert 0.0 <= res["score"] <= 1.0
    assert res["label"] in ["ALTA", "MÉDIA", "BAIXA"]
    assert res["badge"] in ["🟢", "🟡", "🔴"]
