"""Adapter determinístico puro baseado no Método Barsi (sem LLM)."""
from barsi.ports.ai import AIPort
from barsi.domain.models import Position, InsightResult
from barsi.domain.rules import gerar_insight_deterministico

class RuleEngineAdapter(AIPort):
    async def generate_insight(
        self,
        posicao: Position,
        noticias: str,
        memoria_passada: str,
        feedback_usuario: str
    ) -> InsightResult:
        return gerar_insight_deterministico(posicao, noticias)

