"""向量持久化与 pgvector 粗筛；更新时锁事实行以防旧嵌入覆盖新素材。"""

from datetime import datetime
from uuid import UUID

from app.db import Database
from app.llm.embeddings import vector_literal


class EmbeddingRepository:
    def __init__(self, database: Database):
        self._db = database

    async def fingerprints(self, user_id: UUID) -> dict[UUID, tuple[str, str]]:
        async with self._db.connection() as conn:
            rows = await conn.fetch(
                "select v.experience_id, v.source_hash, v.model_key "
                "from public.experience_embeddings v join public.experiences e "
                "on e.id=v.experience_id where e.user_id=$1",
                user_id,
            )
        return {r["experience_id"]: (r["source_hash"], r["model_key"]) for r in rows}

    async def store(
        self,
        user_id: UUID,
        experience_id: UUID,
        updated_at: datetime,
        source_hash: str,
        model_key: str,
        vector: list[float],
    ) -> bool:
        async with self._db.connection() as conn, conn.transaction():
            row = await conn.fetchrow(
                "select updated_at from public.experiences where user_id=$1 and id=$2 for update",
                user_id,
                experience_id,
            )
            if not row or row["updated_at"] != updated_at:
                return False
            await conn.execute(
                "insert into public.experience_embeddings "
                "(experience_id, source_hash, model_key, embedding) values ($1,$2,$3,$4::vector) "
                "on conflict(experience_id) do update set source_hash=excluded.source_hash, "
                "model_key=excluded.model_key, embedding=excluded.embedding, created_at=now()",
                experience_id,
                source_hash,
                model_key,
                vector_literal(vector),
            )
        return True

    async def search(
        self, user_id: UUID, vector: list[float], model_key: str, limit: int
    ) -> dict[UUID, float]:
        async with self._db.connection() as conn:
            rows = await conn.fetch(
                "select v.experience_id, 1-(v.embedding <=> $2::vector) as similarity "
                "from public.experience_embeddings v join public.experiences e "
                "on e.id=v.experience_id where e.user_id=$1 and v.model_key=$3 "
                "order by v.embedding <=> $2::vector, v.experience_id limit $4",
                user_id,
                vector_literal(vector),
                model_key,
                limit,
            )
        return {r["experience_id"]: float(r["similarity"]) for r in rows}
