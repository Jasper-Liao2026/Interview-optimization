"""Idempotent synthetic demo import; never overwrites edited user content."""

from uuid import UUID, uuid5

from app.db import Database
from app.schemas.demo import DemoImportResponse
from app.schemas.jd import JobProfile

DEMO_JD = (
    "【演示岗位】后端工程师：熟悉 Python、FastAPI、PostgreSQL，"
    "负责 API 开发与数据库性能优化，有 Redis 使用经验优先。"
)
DEMO_EXPERIENCES = (
    ("project", "【演示】订单服务", "后端开发", "使用 Python 和 FastAPI 实现订单 API，"
     "使用 PostgreSQL 存储订单，并为查询接口增加索引。",
     ["Python", "FastAPI", "PostgreSQL"]),
    ("internship", "【演示】互联网公司", "后端实习", "使用 Redis 缓存商品信息，"
     "排查 PostgreSQL 慢查询并整理索引优化文档。", ["Redis", "PostgreSQL"]),
    ("campus", "【演示】计算机协会", "技术部成员", "组织 Python 入门分享，"
     "为协会活动制作报名 API。", ["Python", "API"]),
)


class DemoRepository:
    def __init__(self, database: Database):
        self.database = database

    async def import_demo(self, user_id: UUID) -> DemoImportResponse:
        ids = [uuid5(user_id, f"resume-demo-v1-{i}") for i in range(3)]
        jd_id = uuid5(user_id, "resume-demo-jd-v1")
        profile = JobProfile(
            title="【演示岗位】后端工程师", required_skills=["Python", "FastAPI", "PostgreSQL"],
            nice_to_have=["Redis"], keywords=["API", "数据库"], implicit_preferences=[],
            responsibilities=["API 开发", "数据库性能优化"],
        )
        experiences_count = 0
        async with self.database.connection() as conn, conn.transaction():
            await conn.execute(
                "insert into public.profiles(id,display_name,headline) "
                "values($1,'本地用户','') on conflict(id) do nothing", user_id,
            )
            for item_id, (kind, org, role, text, skills) in zip(ids, DEMO_EXPERIENCES, strict=True):
                status = await conn.execute(
                    "insert into public.experiences "
                    "(id,user_id,kind,org,role,raw_description,skill_tags,highlights) "
                    "values($1,$2,$3,$4,$5,$6,$7,$8) on conflict(id) do nothing",
                    item_id, user_id, kind, org, role, text, skills, [text],
                )
                experiences_count += status == "INSERT 0 1"
            status = await conn.execute(
                "insert into public.job_descriptions "
                "(id,user_id,title,raw_text,parsed,parser_model) "
                "values($1,$2,$3,$4,$5,'stub:synthetic-demo-v1') on conflict(id) do nothing",
                jd_id, user_id, profile.title, DEMO_JD, profile.model_dump(mode="json"),
            )
        return DemoImportResponse(
            experience_ids=ids, jd_id=jd_id, inserted_experiences=experiences_count,
            inserted_jobs=int(status == "INSERT 0 1"),
            message="已导入合成演示素材和岗位；不覆盖已有内容，请勿用于正式投递。",
        )
