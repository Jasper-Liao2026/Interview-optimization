"""经历条目接口（M1-2 建立，M2-3 补全 CRUD）。

这一组接口是**素材库**的服务端入口（M2）：
  - `GET    /experiences`        列表（分类分组、组内时间倒序）
  - `POST   /experiences`        新增
  - `PUT    /experiences/{id}`   编辑（全量替换）
  - `GET    /experiences/{id}`   单条
  - `DELETE /experiences/{id}`   删除

另有一条隐式契约：**写路径不接受客户端指定 user_id**。
本项目是本地单机工具，用户由服务端按配置注入（见 deps.get_current_user_id），
让客户端能指定就是一个越权写入的洞。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.deps import CurrentUserDep, ExperienceRepoDep
from app.schemas import (
    ExperienceCreate,
    ExperienceListResponse,
    ExperienceRead,
    ExperienceUpdate,
)

router = APIRouter(prefix="/experiences", tags=["experiences"])


@router.get(
    "",
    response_model=ExperienceListResponse,
    summary="列出经历条目",
    description=(
        "返回当前用户的全部经历，**按分类分组、组内按经历时间倒序**"
        "（进行中的排在最前）。前端按这个顺序直接分组展示即可，不必再排一次。"
    ),
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
    description="素材库的录入入口（M2-4 的录入表单走这里）。",
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


@router.put(
    "/{experience_id}",
    response_model=ExperienceRead,
    summary="编辑一条经历（全量替换）",
    description=(
        "**PUT 语义：全量替换**，不是 PATCH。未提交的字段会按默认值处理"
        "（例如不传 `skill_tags` 等于清空标签），而不是保留旧值。\n\n"
        "编辑表单提交的就是整条记录，全量语义下「必填字段缺失」由 Pydantic 直接拦成 422，"
        "省掉了 PATCH 里「字段没传」与「显式置空」的歧义。\n\n"
        "`user_id` 与时间戳不可改；`updated_at` 由数据库触发器维护。"
    ),
)
async def update_experience(
    experience_id: UUID,
    payload: ExperienceUpdate,
    user_id: CurrentUserDep,
    repository: ExperienceRepoDep,
) -> ExperienceRead:
    row = await repository.update(user_id, experience_id, payload)
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
