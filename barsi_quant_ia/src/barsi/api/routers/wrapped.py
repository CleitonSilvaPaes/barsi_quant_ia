"""Roteador para a Retrospectiva Anual (Barsi Wrapped).

Disponibiliza endpoints de consulta REST e disparo para Telegram.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Response

from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.api.common import get_canonical_positions, get_current_user
from barsi.config import settings
from barsi.infra.logging import logger
from barsi.services.wrapped_service import WrappedService
from barsi.services.wrapped_image_service import WrappedImageService

router = APIRouter(tags=["wrapped"])


@router.get("/api/wrapped/{year}")
async def get_wrapped_data(year: int, request: Request) -> Dict[str, Any]:
    """Retorna os dados estatísticos consolidados da retrospectiva anual."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")

    if not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Ano inválido")

    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        positions = await get_canonical_positions()
    except Exception as exc:
        logger.warning("wrapped_positions_fetch_failed", error=str(exc))
        positions = []

    try:
        retrospective = WrappedService.get_annual_retrospective(
            year=year,
            repo=repo,
            positions=positions,
        )
        return retrospective
    except Exception as exc:
        logger.error("wrapped_generation_failed", year=year, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Erro ao processar retrospectiva: {exc}")


@router.get("/api/wrapped/{year}/image")
async def get_wrapped_image(year: int, request: Request) -> Response:
    """Retorna o card visual em PNG do Barsi Wrapped em alta resolução para download."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")

    if not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Ano inválido")

    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        positions = await get_canonical_positions()
    except Exception:
        positions = []

    try:
        data = WrappedService.get_annual_retrospective(year=year, repo=repo, positions=positions)
        img_bytes = WrappedImageService.generate_image_bytes(data)
        if not img_bytes:
            raise HTTPException(status_code=500, detail="Falha ao renderizar imagem do Wrapped")
        return Response(
            content=img_bytes,
            media_type="image/png",
            headers={"Content-Disposition": f'inline; filename="barsi_wrapped_{year}.png"'}
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("wrapped_image_failed", year=year, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Erro ao gerar imagem: {exc}")


@router.post("/api/wrapped/{year}/telegram")
async def send_wrapped_telegram(year: int, request: Request) -> Dict[str, Any]:
    """Compila o Barsi Wrapped e dispara o card comemorativo e imagem para o Telegram do usuário."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")

    if not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Ano inválido")

    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        positions = await get_canonical_positions()
    except Exception:
        positions = []

    try:
        data = WrappedService.get_annual_retrospective(year=year, repo=repo, positions=positions)
        card_text = WrappedService.generate_telegram_card(data)

        # Envio direto via adapter
        notifier = AdapterFactory.create_notification_adapter(settings)
        await notifier.notify(
            title=f"🎁 Barsi Wrapped {year}",
            body=card_text,
            level="info",
        )

        # Envia também a imagem de Stories se suportado
        img_bytes = WrappedImageService.generate_image_bytes(data)
        photo_sent = False
        if img_bytes and hasattr(notifier, "send_photo"):
            photo_sent = await notifier.send_photo(
                content=img_bytes,
                filename=f"barsi_wrapped_{year}.png",
                caption=f"📸 *Barsi Wrapped {year}* — Seu card de dividendos!",
            )

        return {"ok": True, "year": year, "card": card_text, "photo_sent": photo_sent}
    except Exception as exc:
        logger.error("wrapped_telegram_dispatch_failed", year=year, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Erro ao enviar para Telegram: {exc}")
