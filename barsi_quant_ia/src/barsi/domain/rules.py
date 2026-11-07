"""Regras de Negócio e Princípios Previdenciários Luiz Barsi (B.E.S.T.)."""
from barsi.domain.models import Position, AcaoBarsi, InsightResult
from typing import Tuple

YIELD_MINIMO_BARSI = 0.06  # 6% ao ano em proventos

def calcular_preco_teto(div_proj: float, preco_atual: float) -> float:
    """
    Calcula o Preço Teto Barsi baseado no dividendo por ação (DPA) projetado.
    Fórmula: Teto = DPA / 0.06
    Se o dividendo não estiver informado, adota fallback conservador de 1.3x o preço atual.
    """
    if div_proj and div_proj > 0:
        return round(div_proj / YIELD_MINIMO_BARSI, 2)
    return round(preco_atual * 1.3, 2) if preco_atual > 0 else 0.0

def calcular_margem_seguranca(preco_teto: float, preco_atual: float) -> float:
    """
    Margem de segurança percentual frente ao Preço Teto:
    Fórmula: ((Preço Teto - Preço Atual) / Preço Atual) * 100
    """
    if preco_atual <= 0:
        return 0.0
    margem = ((preco_teto - preco_atual) / preco_atual) * 100
    return round(margem, 2)

def determinar_acao_barsi(margem_seguranca: float) -> AcaoBarsi:
    """
    Determina a ação mandatória conforme a metodologia de acumulação de patrimônio:
    - Margem >= 0%: ACUMULAR (compras contínuas em empresas perenes)
    - Margem < 0%: AGUARDAR (ativo acima do teto estipulado)
    """
    if margem_seguranca >= 0.0:
        return AcaoBarsi.ACUMULAR
    return AcaoBarsi.AGUARDAR

def atualizar_posicao_quant(posicao: Position, preco_novo: float, div_proj: float = None) -> Position:
    """Aplica o recálculo quantitativo puro à posição do portfólio."""
    if preco_novo > 0:
        posicao.preco_atual = preco_novo
    if div_proj is not None and div_proj > 0:
        posicao.div_proj = div_proj

    posicao.preco_teto = calcular_preco_teto(posicao.div_proj, posicao.preco_atual)
    posicao.margem_seguranca = calcular_margem_seguranca(posicao.preco_teto, posicao.preco_atual)
    posicao.status_barsi = determinar_acao_barsi(posicao.margem_seguranca)
    return posicao

def gerar_insight_deterministico(posicao: Position, noticias: str = "", feedback_usuario: str = "") -> InsightResult:
    """
    Engine Quantitativo Avançado:
    - Z-Score e Score Estatístico B.E.S.T. (0-100)
    - Deep Learning para PLN (NLP Léxico-Neural) em notícias da B3
    - Reinforcement Learning (Q-Learning) para calibração da decisão
    """
    from barsi.domain.quant_ai import gerar_insight_quant_avancado
    return gerar_insight_quant_avancado(posicao, noticias=noticias, feedback_usuario=feedback_usuario)

