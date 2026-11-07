"""Testes Unitários para as Regras de Domínio Barsi (B.E.S.T.)."""
import pytest
from barsi.domain.models import Position, AcaoBarsi
from barsi.domain.rules import (
    calcular_preco_teto,
    calcular_margem_seguranca,
    determinar_acao_barsi,
    atualizar_posicao_quant,
    gerar_insight_deterministico
)

def test_calcular_preco_teto():
    # R$ 3.00 dividendo projetado / 0.06 yield = R$ 50.00
    assert calcular_preco_teto(3.00, 30.0) == 50.00
    # R$ 1.50 dividendo projetado / 0.06 yield = R$ 25.00
    assert calcular_preco_teto(1.50, 20.0) == 25.00
    # Fallback quando dividendo é zero: 1.3x preço atual
    assert calcular_preco_teto(0.0, 10.0) == 13.00

def test_calcular_margem_seguranca():
    # Teto 50.00, Preço 40.00 -> Margem = ((50 - 40) / 40) * 100 = 25%
    assert calcular_margem_seguranca(50.0, 40.0) == 25.00
    # Teto 40.00, Preço 50.00 -> Margem = ((40 - 50) / 50) * 100 = -20%
    assert calcular_margem_seguranca(40.0, 50.0) == -20.00
    # Preço zero não deve causar divisão por zero
    assert calcular_margem_seguranca(50.0, 0.0) == 0.0

def test_determinar_acao_barsi():
    # Margem positiva -> ACUMULAR
    assert determinar_acao_barsi(15.0) == AcaoBarsi.ACUMULAR
    assert determinar_acao_barsi(0.0) == AcaoBarsi.ACUMULAR
    # Margem negativa -> AGUARDAR
    assert determinar_acao_barsi(-5.0) == AcaoBarsi.AGUARDAR

def test_atualizar_posicao_quant():
    pos = Position(ticker="BBAS3", row_index=7, preco_atual=25.0, div_proj=3.0)
    # Teto esperado: 3.0 / 0.06 = 50.0. Preço novo: 30.0 -> Margem: ((50-30)/30)*100 = 66.67%
    atualizada = atualizar_posicao_quant(pos, preco_novo=30.0)
    assert atualizada.preco_atual == 30.0
    assert atualizada.preco_teto == 50.0
    assert atualizada.margem_seguranca == 66.67
    assert atualizada.status_barsi == AcaoBarsi.ACUMULAR

def test_gerar_insight_deterministico():
    pos = Position(ticker="TAEE11", row_index=8, preco_atual=35.0, preco_teto=45.0, margem_seguranca=28.57)
    res = gerar_insight_deterministico(pos)
    assert res.ticker == "TAEE11"
    assert res.acao_sugerida == AcaoBarsi.ACUMULAR
    assert "ACUMULAR" in res.insight
    assert res.fallback_used is True

