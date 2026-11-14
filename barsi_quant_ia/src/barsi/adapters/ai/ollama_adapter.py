"""Adapter para Ollama Remoto (ex: PC do usuário ou servidor local) com fallback."""
import httpx
import time
from barsi.ports.ai import AIPort
from barsi.domain.models import Position, InsightResult, AcaoBarsi
from barsi.domain.rules import gerar_insight_deterministico
from barsi.infra.logging import logger

class OllamaRemoteAdapter(AIPort):
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "gemma2:2b", timeout_sec: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_sec = timeout_sec

    async def generate_insight(
        self,
        posicao: Position,
        noticias: str,
        memoria_passada: str,
        feedback_usuario: str
    ) -> InsightResult:
        # Pré-cálculos determinísticos em Python para não sobrecarregar modelos < 1B
        preco = posicao.preco_atual
        custo_medio = getattr(posicao, "custo_medio", 0.0) or preco
        rentabilidade_pct = ((preco - custo_medio) / custo_medio * 100.0) if custo_medio > 0 else 0.0
        div_proj = posicao.div_proj or 0.0
        dy_real = (div_proj / preco * 100.0) if preco > 0 else 0.0
        yoc_real = (div_proj / custo_medio * 100.0) if custo_medio > 0 else dy_real
        teto = posicao.preco_teto
        teto_manual = bool(getattr(posicao, "teto_manual", False))

        # Prompt ultra-otimizado para modelos leves (< 1B / llama.cpp / Qwen2.5-0.5B / SmolLM2)
        prompt = f"""Você é um analista financeiro rigoroso do Método Barsi. Analise os dados JSON abaixo em no máximo 3 frases curtas e diretas em português.

Regras estritas:
1. Se DY_REAL for menor que 6.0%, classifique o ativo como AGUARDAR (mesmo que o preço esteja abaixo do teto manual).
2. Se PRECO < CUSTO_MEDIO e DY_REAL >= 6.0%, classifique como OPORTUNIDADE DE PREÇO MÉDIO.
3. Se TETO_MANUAL for true e DIV_PROJ for 0.0, avise que o teto está desatualizado.
4. Nunca invente números. Use apenas os valores fornecidos.

Dados do Ativo:
- Ticker: {posicao.ticker}
- Preço Atual: R$ {preco:.2f} | Preço Médio: R$ {custo_medio:.2f} (Lucro/Prejuízo: {rentabilidade_pct:+.1f}%)
- Teto Manual: R$ {teto:.2f} (Teto Manual Ativo: {teto_manual})
- Dividendo Projetado: R$ {div_proj:.2f} | DY Real: {dy_real:.1f}% | YoC Real: {yoc_real:.1f}%

Saída esperada (siga exatamente este formato):
Diretriz: [ACUMULAR / AGUARDAR / CAUTELA]
Sustentabilidade: [ALTA / MÉDIA / BAIXA]
Análise: [Sua análise crítica em 2 frases]
"""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 120}
        }

        start_time = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(f"{self.base_url}/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
                texto = data.get("response", "").strip()
                latency_ms = int((time.time() - start_time) * 1000)

                # Regras de proteção do capital contra alucinação de modelos < 1B
                if dy_real < 6.0 or (teto_manual and div_proj <= 0.0):
                    acao = AcaoBarsi.AGUARDAR
                elif preco < custo_medio and dy_real >= 6.0:
                    acao = AcaoBarsi.ACUMULAR
                elif "ACUMULAR" in texto.upper() and posicao.margem_seguranca >= 0:
                    acao = AcaoBarsi.ACUMULAR
                else:
                    acao = AcaoBarsi.AGUARDAR

                return InsightResult(
                    ticker=posicao.ticker,
                    insight=texto,
                    acao_sugerida=acao,
                    provider="ollama_remote",
                    model=self.model,
                    latency_ms=latency_ms,
                    fallback_used=False
                )
        except Exception as exc:
            logger.warn("ollama_failed_falling_back_to_rules", ticker=posicao.ticker, error=str(exc))
            # Fallback determinístico instantâneo
            fb = gerar_insight_deterministico(posicao, noticias)
            fb.fallback_used = True
            return fb

