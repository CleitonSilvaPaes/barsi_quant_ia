"""Modelos puros de domínio do Sistema Previdenciário Barsi."""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum

class AcaoBarsi(str, Enum):
    ACUMULAR = "ACUMULAR"
    AGUARDAR = "AGUARDAR"
    MANTER = "MANTER"

class Position(BaseModel):
    ticker: str
    row_index: int
    empresa: str = ""
    setor: str = ""
    qtd_antes: int = 0
    qtd_aporte: int = 0
    qtd_total: int = 0
    preco_atual: float = 0.0
    div_proj: float = 0.0
    preco_teto: float = 0.0
    margem_seguranca: float = 0.0
    dy: float = 0.0
    valor_total: float = 0.0
    total_aporte: float = 0.0
    status_barsi: AcaoBarsi = AcaoBarsi.AGUARDAR
    feedback_usuario: str = ""
    insight_atual: str = ""

class SimuladorOrder(BaseModel):
    rank: int
    ticker: str
    empresa: str
    setor: str
    cotacao: float
    preco_teto: float
    margem_seg: float
    alocacao_meta_pct: float
    orcamento_teorico: float
    qtd_fracionaria: int
    gasto_efetivo: float
    troco: float
    provento_anual_adic: float
    yield_compra_pct: float
    status_ordem: str

class MarketQuote(BaseModel):
    ticker: str
    price: float
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str = "unknown"

class NewsSummary(BaseModel):
    ticker: str
    headlines: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str = "rss"

class InsightResult(BaseModel):
    ticker: str
    insight: str
    acao_sugerida: AcaoBarsi
    provider: str
    model: str = "none"
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    fallback_used: bool = False

class RunSummary(BaseModel):
    run_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tickers_analyzed: int = 0
    positions_updated: int = 0
    duration_ms: int = 0
    memory_peak_mb: float = 0.0
    errors: List[str] = Field(default_factory=list)

