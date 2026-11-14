"""Regras de Negócio e Princípios Previdenciários Luiz Barsi (B.E.S.T.)."""
from barsi.domain.models import Position, AcaoBarsi, InsightResult
from typing import Tuple

YIELD_MINIMO_BARSI = 0.06        # Ações perenes B.E.S.T. (Bancos, Energia, Saneamento, Seguros, Telecom)
YIELD_MINIMO_FII_TIJOLO = 0.08   # FIIs de tijolo / logístico / shoppings (HGLG11, KNRI11, XPLG11)
YIELD_MINIMO_FII_PAPEL = 0.10    # FIIs de papel / CRI / Recebíveis (MXRF11, KNCR11, HGCR11)
YIELD_MINIMO_FII_FOF = 0.09      # Fundos de fundos / híbridos (BCFF11, XPML11)

def is_fii(ticker: str, setor: str = "") -> bool:
    """Verifica se o ticker é um Fundo Imobiliário (FII), excluindo units de ações B.E.S.T."""
    t = ticker.upper()
    s = setor.upper()
    if "FII" in s:
        return True
    unit_stocks = ["TAEE", "SANB", "SAPR", "ALUP", "CPLE", "KLBN", "BPAC", "SULA"]
    if any(u in t for u in unit_stocks):
        return False
    return t.endswith("11")

def _yield_target_para(ticker: str, setor: str = "") -> float:
    """Retorna a taxa de corte / hurdle yield para cálculo do Preço Teto por classe de ativo."""
    if not is_fii(ticker, setor):
        return YIELD_MINIMO_BARSI
    setor_l = setor.lower()
    if any(k in setor_l for k in ["papel", "cri", "receb", "híbrid", "hibrid"]):
        return YIELD_MINIMO_FII_PAPEL
    if any(k in setor_l for k in ["fof", "fundo de fundo"]):
        return YIELD_MINIMO_FII_FOF
    return YIELD_MINIMO_FII_TIJOLO

def calcular_preco_teto(
    div_proj: float,
    preco_atual: float,
    ticker: str = "",
    setor: str = ""
) -> float:
    """
    Calcula o Preço Teto Barsi calibrado pela classe do ativo:
    - Ações B.E.S.T.: DPA / 0.06 (Hurdle 6% de empresas perenes que reinvestem lucros)
    - FII de Tijolo: DPA / 0.08 (Hurdle 8%)
    - FII de FoF: DPA / 0.09 (Hurdle 9%)
    - FII de Papel: DPA / 0.10 (Hurdle 10% de recebíveis com payout 95%+)
    Se o dividendo não estiver informado, adota fallback conservador de 1.3x o preço atual.
    """
    if div_proj and div_proj > 0:
        yield_target = _yield_target_para(ticker, setor)
        return round(div_proj / yield_target, 2)
    return round(preco_atual * 1.3, 2) if preco_atual > 0 else 0.0

def calcular_margem_seguranca(preco_teto: float, preco_atual: float) -> float:
    """
    Margem de segurança percentual (desconto real frente ao Preço Teto):
    Fórmula de Análise de Valor: ((Preço Teto - Preço Atual) / Preço Teto) * 100
    """
    if preco_teto <= 0:
        return 0.0
    margem = ((preco_teto - preco_atual) / preco_teto) * 100
    return round(margem, 2)

def determinar_acao_barsi(margem_seguranca: float, dy_real: float = 6.0, div_proj: float = 1.0) -> AcaoBarsi:
    """
    Determina a ação mandatória conforme a metodologia Barsi:
    - Regra 1: Se DY_REAL < 6.0% ou div_proj <= 0, classifique como AGUARDAR
      (mesmo que o preço esteja abaixo do teto manual).
    - Regra 2: Margem >= 0% e DY >= 6%: ACUMULAR
    - Regra 3: Margem < 0%: AGUARDAR
    """
    if div_proj <= 0.0 or dy_real < 6.0:
        return AcaoBarsi.AGUARDAR
    if margem_seguranca >= 0.0:
        return AcaoBarsi.ACUMULAR
    return AcaoBarsi.AGUARDAR

def atualizar_posicao_quant(posicao: Position, preco_novo: float, div_proj: float = None) -> Position:
    """Aplica o recálculo quantitativo puro à posição do portfólio."""
    if preco_novo > 0:
        posicao.preco_atual = preco_novo
    if div_proj is not None and div_proj > 0:
        posicao.div_proj = div_proj

    # Se teto_manual estiver ativo e com valor positivo, preserva o teto estipulado pelo usuário
    if not getattr(posicao, "teto_manual", False) or posicao.preco_teto <= 0:
        posicao.preco_teto = calcular_preco_teto(
            posicao.div_proj,
            posicao.preco_atual,
            ticker=posicao.ticker,
            setor=posicao.setor
        )

    dy_real = ((posicao.div_proj or 0.0) / posicao.preco_atual * 100.0) if posicao.preco_atual > 0 else 0.0
    posicao.margem_seguranca = calcular_margem_seguranca(posicao.preco_teto, posicao.preco_atual)
    posicao.status_barsi = determinar_acao_barsi(
        posicao.margem_seguranca,
        dy_real=dy_real,
        div_proj=posicao.div_proj or 0.0
    )
    if hasattr(posicao, "decisao"):
        posicao.decisao = posicao.status_barsi.value
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

