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
        prompt = f"""Você é o consultor de investimentos baseado no Método Barsi (B.E.S.T.).
Analise o ativo {posicao.ticker}:
- Cotação Atual: R$ {posicao.preco_atual:.2f}
- Preço Teto Barsi (Yield 6%): R$ {posicao.preco_teto:.2f}
- Margem de Segurança: {posicao.margem_seguranca:.1f}%
- Notícias Recentes: {noticias}
- Memória Histórica: {memoria_passada}
- Instrução do Usuário: {feedback_usuario}

Diretrizes:
1. Respeite as preferências ensinadas pelo usuário no feedback.
2. Diga sucintamente se a tese segue sólida e a ação: ACUMULAR ou AGUARDAR.
3. Máximo de 2 frases objetivas.
"""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 100}
        }

        start_time = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(f"{self.base_url}/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
                texto = data.get("response", "").strip()
                latency_ms = int((time.time() - start_time) * 1000)

                acao = AcaoBarsi.ACUMULAR if "ACUMULAR" in texto.upper() or posicao.margem_seguranca >= 0 else AcaoBarsi.AGUARDAR
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

