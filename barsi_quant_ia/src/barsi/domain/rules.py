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

def gerar_insight_deterministico(posicao: Position, noticias: str = "") -> InsightResult:
    """
    Engine de Regras 100% determinístico (RuleEngineAdapter).
    Funciona sem qualquer dependência de modelos neurais ou conexão com IA.
    """
    margem = posicao.margem_seguranca
    teto = posicao.preco_teto
    ticker = posicao.ticker

    if margem >= 20.0:
        texto = f"{ticker} apresenta margem de segurança de {margem:.1f}% frente ao teto de R$ {teto:.2f}. Momento defensivo favorável para ACUMULAR."
        acao = AcaoBarsi.ACUMULAR
    elif margem >= 0.0:
        texto = f"{ticker} cotado abaixo do Preço Teto (R$ {teto:.2f}) com yield superior a 6%. Recomenda-se ACUMULAÇÃO contínua e moderada."
        acao = AcaoBarsi.ACUMULAR
    else:
        texto = f"{ticker} ultrapassou o Preço Teto Barsi de R$ {teto:.2f} (margem {margem:.1f}%). Recomenda-se AGUARDAR e redirecionar aportes para ativos com desconto."
        acao = AcaoBarsi.AGUARDAR

    return InsightResult(
        ticker=ticker,
        insight=texto,
        acao_sugerida=acao,
        provider="rule_engine",
        model="deterministic_best",
        latency_ms=1,
        fallback_used=True
    )

