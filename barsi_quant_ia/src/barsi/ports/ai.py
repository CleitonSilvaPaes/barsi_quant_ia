"""Porta formal para Motores de Inteligência e Geração de Insights."""
from typing import Protocol, runtime_checkable
from barsi.domain.models import Position, InsightResult

@runtime_checkable
class AIPort(Protocol):
    async def generate_insight(
        self,
        posicao: Position,
        noticias: str,
        memoria_passada: str,
        feedback_usuario: str
    ) -> InsightResult:
        """Gera parecer em texto fundamentado nas regras Barsi e feedback do usuário."""
        ...

