"""Import clearly labelled sample data without a model key."""

from fastapi import APIRouter

from app.deps import CurrentUserDep, DatabaseDep
from app.repositories.demo_repo import DemoRepository
from app.schemas.demo import DemoImportResponse

router = APIRouter(prefix="/system", tags=["demo"])


@router.post("/demo", response_model=DemoImportResponse)
async def import_demo(user_id: CurrentUserDep, database: DatabaseDep) -> DemoImportResponse:
    return await DemoRepository(database).import_demo(user_id)
