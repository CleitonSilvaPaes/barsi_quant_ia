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

def test_calcular_preco_teto_fii_e_units():
    # MXRF11: FII de papel -> Hurdle 10% -> 1.08 / 0.10 = R$ 10.80
    assert calcular_preco_teto(1.08, 8.80, ticker="MXRF11", setor="FII - Papel") == 10.80

    # HGLG11: FII de tijolo -> Hurdle 8% -> 13.20 / 0.08 = R$ 165.00
    assert calcular_preco_teto(13.20, 160.0, ticker="HGLG11", setor="FII - Tijolo") == 165.00

    # TAEE11: Unit de Energia B.E.S.T. -> Hurdle 6% -> 2.70 / 0.06 = R$ 45.00
    assert calcular_preco_teto(2.70, 36.0, ticker="TAEE11", setor="Energia") == 45.00

    # BBAS3: Ação Banco B.E.S.T. -> Hurdle 6% -> 2.30 / 0.06 = R$ 38.33
    assert calcular_preco_teto(2.30, 17.13, ticker="BBAS3", setor="Bancos") == 38.33

def test_atualizar_posicao_quant_teto_manual():
    # Posição com teto manual estipulado pelo usuário
    pos = Position(
        ticker="MXRF11",
        row_index=13,
        preco_atual=8.80,
        div_proj=1.08,
        preco_teto=11.50,
        teto_manual=True
    )
    atualizada = atualizar_posicao_quant(pos, preco_novo=9.00)
    # Deve preservar o teto manual de 11.50 e não recomputar para 10.80
    assert atualizada.preco_teto == 11.50
    # Margem deve ser calculada sobre o teto manual: ((11.50 - 9.00) / 9.00) * 100 = 27.78%
    assert atualizada.margem_seguranca == 27.78
    assert atualizada.status_barsi == AcaoBarsi.ACUMULAR


