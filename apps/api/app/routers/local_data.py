"""Single-user local data export and explicit erasure."""

from fastapi import APIRouter, Depends, HTTPException, Response

from app.data_activity import protect_local_data
from app.deps import CurrentUserDep, DatabaseDep
from app.repositories.local_data_repo import LocalDataBusyError, LocalDataRepository
from app.schemas.local_data import LocalDataClearRequest, LocalDataClearResponse, LocalDataExport

router = APIRouter(prefix="/system/data", tags=["local-data"])


@router.get(
    "/export",
    response_model=LocalDataExport,
    summary="导出全部本地数据为 JSON",
    dependencies=[Depends(protect_local_data)],
)
async def export_local_data(
    user_id: CurrentUserDep, database: DatabaseDep, response: Response
) -> LocalDataExport:
    response.headers["Content-Disposition"] = 'attachment; filename="resume-optimizer-data.json"'
    response.headers["Cache-Control"] = "no-store"
    return await LocalDataRepository(database).export(user_id)


@router.post("/clear", response_model=LocalDataClearResponse, summary="清空本地数据")
async def clear_local_data(
    payload: LocalDataClearRequest,
    user_id: CurrentUserDep,
    database: DatabaseDep,
    response: Response,
) -> LocalDataClearResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await LocalDataRepository(database).clear(user_id)
    except LocalDataBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
