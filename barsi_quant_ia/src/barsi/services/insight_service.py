"""Serviço de geração de pareceres e enriquecimento de memória."""
from barsi.ports.ai import AIPort
from barsi.ports.history import HistoryPort
from barsi.domain.models import Position, InsightResult

class InsightService:
    def __init__(self, ai_port: AIPort, history_port: HistoryPort):
        self.ai = ai_port
        self.history = history_port

    async def generate_position_insight(self, posicao: Position, noticias: str) -> InsightResult:
        # Recupera memória passada no banco
        last = await self.history.get_last_analysis(posicao.ticker)
        if last:
            data, last_insight, last_feedback = last
            memoria = f"Em {data}: '{last_insight}'"
            feedback = f"Feedback passado: '{last_feedback}' | Feedback atual: '{posicao.feedback_usuario}'"
        else:
            memoria = "Primeira análise histórica deste ativo."
            feedback = f"Instrução informada: '{posicao.feedback_usuario}'"

        return await self.ai.generate_insight(
            posicao=posicao,
            noticias=noticias,
            memoria_passada=memoria,
            feedback_usuario=feedback
        )

