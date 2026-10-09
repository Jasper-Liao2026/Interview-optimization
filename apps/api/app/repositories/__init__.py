"""仓储层（repository）。

对外暴露「领域对象进、领域对象出」的方法，SQL 全部收在各自模块里。
上层（routers / agents）不写 SQL —— 这样从 asyncpg 换到 SQLAlchemy（M1-1 备忘里写的）
只改这一层，接口不变。
"""

from app.repositories.experience_repo import ExperienceRepository
from app.repositories.jd_repo import JobDescriptionRepository
from app.repositories.profile_repo import ProfileRepository
from app.repositories.resume_repo import ResumeRepository

__all__ = [
    "ExperienceRepository",
    "JobDescriptionRepository",
    "ProfileRepository",
    "ResumeRepository",
]
