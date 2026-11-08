"""Porta formal para Repositório de Portfólio (Google Sheets / Excel)."""
from typing import Protocol, runtime_checkable, List
from barsi.domain.models import Position

@runtime_checkable
class SheetsPort(Protocol):
    async def load_positions(self) -> List[Position]:
        """Lê os ativos cadastrados no portfólio."""
        ...

    async def update_positions(self, positions: List[Position], invalidate: bool = True) -> None:
        """Persiste preços, margens, status e insights atualizados."""
        ...

