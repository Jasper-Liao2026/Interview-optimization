"""经历条目接口（M1-2）。

M1 阶段没有 UI 表单（M2-4 才做），这一组接口的用途是：
  1. `POST` —— 用脚本 / curl 把素材写进去（task 里说的「用户先硬编码」）
  2. `GET`  —— 前端生成页里让用户勾选要用哪几段经历
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.deps import CurrentUserDep, ExperienceRepoDep
from app.schemas import ExperienceCreate, ExperienceListResponse, ExperienceRead

router = APIRouter(prefix="/experiences", tags=["experiences"])


@router.get(
    "",
    response_model=ExperienceListResponse,
    summary="列出经历条目",
    description="返回当前用户的全部经历，按分类与 sort_order 排序（前端可直接分组展示）。",
)
async def list_experiences(
    user_id: CurrentUserDep, repository: ExperienceRepoDep
) -> ExperienceListResponse:
    rows = await repository.list_for_user(user_id)
    items = [ExperienceRead.model_validate(row) for row in rows]
    return ExperienceListResponse(items=items, total=len(items))


@router.post(
    "",
    response_model=ExperienceRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增一条经历",
    description="M1 的最小录入入口，供脚本写入素材。UI 表单是 M2-4。",
)
async def create_experience(
    payload: ExperienceCreate, user_id: CurrentUserDep, repository: ExperienceRepoDep
) -> ExperienceRead:
    row = await repository.create(user_id, payload)
    return ExperienceRead.model_validate(row)


@router.get(
    "/{experience_id}",
    response_model=ExperienceRead,
    summary="读取一条经历",
)
async def get_experience(
    experience_id: UUID, user_id: CurrentUserDep, repository: ExperienceRepoDep
) -> ExperienceRead:
    row = await repository.get(user_id, experience_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="经历条目不存在")
    return ExperienceRead.model_validate(row)


@router.delete(
    "/{experience_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除一条经历",
)
async def delete_experience(
    experience_id: UUID, user_id: CurrentUserDep, repository: ExperienceRepoDep
) -> None:
    if not await repository.delete(user_id, experience_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="经历条目不存在")
