"""Porta formal para Repositório de Memória Histórica e Backup."""
from typing import Protocol, runtime_checkable, Optional, Tuple, List
from barsi.domain.models import Position, InsightResult, RunSummary

@runtime_checkable
class HistoryPort(Protocol):
    async def get_last_analysis(self, ticker: str) -> Optional[Tuple[str, str, str]]:
        """Retorna (data_analise, insight_passado, feedback_passado)."""
        ...

    async def save_analysis(
        self,
        run_id: str,
        posicao: Position,
        insight: InsightResult,
        noticias: str
    ) -> None:
        """Grava a análise no histórico local."""
        ...

    async def record_run(self, summary: RunSummary) -> None:
        """Registra a conclusão do ciclo de execução."""
        ...

