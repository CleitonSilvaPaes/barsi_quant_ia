"""Rotas para gerenciamento e renderização de Artefatos Dinâmicos Personalizados."""
import io
import sqlite3
from typing import Optional
import pandas as pd
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

from barsi.config import settings
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.api.common import (
    get_current_user,
    require_role_action,
    ArtifactCreateRequest,
)

router = APIRouter(tags=["artifacts"])


@router.get("/api/artifacts/sources")
async def get_artifact_sources(request: Request):
    """Lista as fontes de dados disponíveis e seus respectivos esquemas para geração de artefatos."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    return repo.get_available_data_sources()


@router.get("/api/artifacts")
async def list_artifacts(request: Request, visible_in: Optional[str] = None):
    """Lista todos os artefatos cadastrados com opção de filtragem por contexto de visualização."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    items = repo.list_custom_artifacts(visible_in=visible_in)
    return {"items": items}


@router.post("/api/artifacts")
async def create_artifact(request: Request, payload: ArtifactCreateRequest):
    """Cria uma nova definição de artefato personalizado dinâmico."""
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        artifact_id = repo.create_custom_artifact(
            name=payload.name,
            chart_type=payload.chart_type,
            data_source=payload.data_source,
            fields=payload.fields,
            description=payload.description,
            filters=payload.filters,
            visible_in=payload.visible_in
        )
        return {"ok": True, "id": artifact_id, "message": "Artefato criado com sucesso"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Já existe um artefato com o nome '{payload.name}'")


@router.delete("/api/artifacts/{artifact_id}")
async def delete_artifact(request: Request, artifact_id: int):
    """Exclui uma definição de artefato existente."""
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    deleted = repo.delete_custom_artifact(artifact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Artefato não encontrado")
    return {"ok": True, "message": "Artefato excluído com sucesso"}


@router.get("/api/artifacts/{artifact_id}/data")
async def get_artifact_data(request: Request, artifact_id: int):
    """Executa a query do artefato e retorna os dados brutos estruturados para plotagem."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        return repo.get_custom_artifact_data(artifact_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/artifacts/{artifact_id}/export")
async def export_artifact_excel(request: Request, artifact_id: int):
    """Exporta sob demanda os dados tabulares do artefato em formato Excel (.xlsx)."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        data = repo.get_custom_artifact_data(artifact_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    rows = data.get("rows") or []
    if rows:
        df = pd.DataFrame(rows)
    else:
        df = pd.DataFrame(columns=data.get("fields") or ["dados"])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Dados", index=False)
    output.seek(0)

    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in data.get("name", f"artefato_{artifact_id}"))
    filename = f"{safe_name}_{artifact_id}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
