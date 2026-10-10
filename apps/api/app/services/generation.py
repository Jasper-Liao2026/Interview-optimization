"""Durable M4 generation, recovery and isolated retry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

from app.agents.assembler import build_entry
from app.agents.checkpoint import GenerationRunStore, RunConflictError
from app.agents.generation_graph import GenerationGraph
from app.agents.jd_parser import JdParser
from app.agents.rewriter import ExperienceRewriter
from app.config import Settings
from app.llm import LlmClient
from app.observability import Observability
from app.schemas import GenerateRequest, JobProfile, RewrittenExperience


class GenerationError(RuntimeError):
    pass


class NoExperiencesError(GenerationError):
    pass


class InvalidJobError(GenerationError):
    pass


class RunNotFoundError(GenerationError):
    pass


@dataclass(slots=True)
class GenerationOutcome:
    resume: dict[str, Any]
    profile: JobProfile
    jd_id: UUID | None
    provider: str
    model: str
    is_stub: bool
    trace_id: str
    run_id: UUID
    items: list[dict[str, Any]]
    failures: list[dict[str, Any]]
    checkpoint: dict[str, Any]
    persisted: bool
    warnings: list[str] = field(default_factory=list)


def json_row(row: dict[str, Any]) -> dict[str, Any]:
    def encode(value: Any) -> Any:
        if isinstance(value, (UUID, date, datetime)):
            return str(value)
        if isinstance(value, dict):
            return {k: encode(v) for k, v in value.items()}
        if isinstance(value, list):
            return [encode(v) for v in value]
        return value

    return encode(row)


class ResumeGenerationService:
    def __init__(
        self,
        *,
        settings: Settings,
        experiences: Any,
        job_descriptions: Any,
        resumes: Any,
        profiles: Any,
        parser: JdParser,
        rewriter: ExperienceRewriter,
        llm: LlmClient,
        observability: Observability,
        runs: GenerationRunStore | None = None,
    ) -> None:
        self._settings, self._experiences, self._jds = settings, experiences, job_descriptions
        self._resumes, self._profiles = resumes, profiles
        self._parser, self._rewriter, self._llm, self._obs = parser, rewriter, llm, observability
        self._runs = runs or GenerationRunStore()

    async def generate(
        self, user_id: UUID, request: GenerateRequest, trace_id: str
    ) -> GenerationOutcome:
        run_id = request.run_id or uuid4()
        async with self._runs.lock(run_id):
            existing = await self._runs.get(user_id, run_id)
            body = request.model_dump(mode="json", exclude={"run_id"})
            if existing:
                if existing["request"] != body:
                    raise RunConflictError("run_id 已用于不同的生成请求")
                return await self._execute(user_id, run_id, existing)
            rows = (
                await self._experiences.list_by_ids(user_id, request.experience_ids)
                if request.experience_ids
                else await self._experiences.list_for_user(user_id)
            )
            if not rows:
                raise NoExperiencesError("没有任何经历素材，无法生成简历，请先录入经历素材。")
            if request.experience_ids:
                by_id = {row["id"]: row for row in rows}
                if set(request.experience_ids) != set(by_id):
                    raise NoExperiencesError("选中的经历素材已删除，请重新选择。")
                rows = [by_id[i] for i in request.experience_ids]
            header_row = await self._profiles.ensure(
                user_id,
                display_name=self._settings.dev_user_name,
                headline=self._settings.dev_user_headline,
            )
            run = {
                "request": body,
                "trace_id": trace_id,
                "experiences": [json_row(row) for row in rows],
                "header": {"name": header_row["display_name"], "headline": header_row["headline"]},
                "resume_id": str(uuid4()),
                "status": "pending",
                "warnings": [],
                "provider": self._llm.provider,
                "model": self._llm.model,
                "is_stub": self._llm.is_stub,
            }
            await self._runs.save(user_id, run_id, run)
            return await self._execute(user_id, run_id, run)

    async def _prepare(self, user_id: UUID, run_id: UUID, run: dict[str, Any]) -> None:
        request = run["request"]
        if "profile" not in run:
            if request["jd_id"]:
                row = await self._jds.get(user_id, UUID(request["jd_id"]))
                if not row or not row["parsed"]:
                    raise InvalidJobError("JD 不存在或尚未解析，请重新选择已保存的岗位。")
                profile = JobProfile.model_validate(row["parsed"])
                run["jd_id"] = request["jd_id"]
            else:
                parsed = await self._parser.parse(request["jd_text"])
                profile = parsed.value
                run["warnings"].extend(parsed.warnings)
                run["parser_model"] = parsed.llm.model
            run["profile"] = profile.model_dump(mode="json")
            await self._runs.save(user_id, run_id, run)
        if request["persist"] and not run.get("jd_id"):
            profile = JobProfile.model_validate(run["profile"])
            row = await self._jds.create(
                user_id,
                raw_text=request["jd_text"],
                title=profile.title,
                company=profile.company,
                parsed=run["profile"],
                parser_model=run.get("parser_model"),
                jd_id=uuid5(run_id, "parsed-jd"),
            )
            run["jd_id"] = str(row["id"])
            await self._runs.save(user_id, run_id, run)

    async def _execute(
        self, user_id: UUID, run_id: UUID, run: dict[str, Any], *, retry: int | None = None
    ) -> GenerationOutcome:
        await self._prepare(user_id, run_id, run)
        async with self._runs.checkpointer() as saver:
            graph = GenerationGraph(
                self._rewriter,
                checkpointer=saver,
                observability=self._obs,
                max_concurrency=self._settings.rewrite_max_concurrency,
            )
            config = graph.config(str(run_id))
            snapshot = await graph.graph.aget_state(config)
            if retry is not None:
                if snapshot.next:
                    raise RunConflictError("生成尚未结束，请先恢复任务")
                item = snapshot.values.get("items", {}).get(str(retry))
                if item is None or item["status"] != "failed":
                    raise RunConflictError("只能重试失败条目")
                input_state = {"selected": [retry]}
            elif snapshot.next:
                input_state = None
            elif snapshot.values:
                run["graph"] = snapshot.values
                return await self._finish(user_id, run_id, run)
            else:
                input_state = {
                    "profile": run["profile"],
                    "experiences": run["experiences"],
                    "selected": list(range(len(run["experiences"]))),
                    "items": {},
                }
            run["status"] = "running"
            await self._runs.save(user_id, run_id, run)
            state = await graph.graph.ainvoke(input_state, config=config, durability="sync")
            run["graph"] = state
        return await self._finish(user_id, run_id, run)

    async def _finish(self, user_id: UUID, run_id: UUID, run: dict[str, Any]) -> GenerationOutcome:
        state = run.get("graph", {})
        items = list(state.get("items", {}).values())
        failures = [item for item in items if item["status"] == "failed"]
        run["status"] = (
            "partial"
            if failures and len(failures) < len(items)
            else ("failed" if failures else "completed")
        )
        profile = JobProfile.model_validate(run["profile"])
        now = datetime.now(UTC).isoformat()
        row = {
            "id": run["resume_id"],
            "user_id": str(user_id),
            "jd_id": run.get("jd_id"),
            "title": run["request"]["title"] or profile.title or "未命名简历",
            "template": "classic",
            "header": run["header"],
            "sections": state.get("sections", []),
            "status": "draft",
            "generator": f"{run['provider']}:{run['model']}",
            "created_at": now,
            "updated_at": now,
        }
        if run["request"]["persist"]:
            row = await self._resumes.create(
                user_id,
                jd_id=UUID(run["jd_id"]) if run.get("jd_id") else None,
                title=row["title"],
                template="classic",
                header=run["header"],
                sections=row["sections"],
                generator=row["generator"],
                resume_id=UUID(run["resume_id"]),
            )
        run["resume"] = json_row(row)
        await self._runs.save(user_id, run_id, run)
        return self._outcome(run_id, run)

    def _outcome(self, run_id: UUID, run: dict[str, Any]) -> GenerationOutcome:
        state = run.get("graph", {})
        items, failures, warnings = [], [], list(run["warnings"])
        for i, experience in enumerate(run["experiences"]):
            item = state.get("items", {}).get(str(i), {"status": "pending"})
            visible = {
                "index": i,
                "experience_id": experience["id"],
                "org": experience["org"],
                "status": item["status"],
            }
            warnings.extend(item.get("warnings", []))
            if item["status"] == "succeeded":
                source = dict(experience)
                for key in ("start_date", "end_date"):
                    if source.get(key):
                        source[key] = date.fromisoformat(source[key])
                visible["entry"] = build_entry(
                    source, RewrittenExperience.model_validate(item["result"])
                ).model_dump(mode="json")
            elif item["status"] == "failed":
                failures.append(
                    {
                        "index": i,
                        "experience_id": experience["id"],
                        "error": item["error"],
                        "details": item.get("details", []),
                        "retryable": True,
                    }
                )
            items.append(visible)
        if not run["request"]["persist"]:
            warnings.append("persist=false：未落库，返回的简历 id 无法用于预览或导出")
        return GenerationOutcome(
            resume=run["resume"],
            profile=JobProfile.model_validate(run["profile"]),
            jd_id=UUID(run["jd_id"]) if run.get("jd_id") else None,
            provider=run["provider"],
            model=run["model"],
            is_stub=run["is_stub"],
            trace_id=run["trace_id"],
            run_id=run_id,
            items=items,
            failures=failures,
            checkpoint={
                "status": run["status"],
                "updated_at": run["updated_at"],
                "backend": self._runs.backend,
                "resumable": run["status"] in ("pending", "running"),
            },
            persisted=run["request"]["persist"],
            warnings=list(dict.fromkeys(warnings)),
        )

    async def resume(
        self, user_id: UUID, run_id: UUID, *, retry: int | None = None
    ) -> GenerationOutcome:
        async with self._runs.lock(run_id):
            run = await self._runs.get(user_id, run_id)
            if run is None:
                raise RunNotFoundError("生成任务不存在")
            return await self._execute(user_id, run_id, run, retry=retry)

    async def read(self, user_id: UUID, run_id: UUID) -> GenerationOutcome:
        run = await self._runs.get(user_id, run_id)
        if run is None:
            raise RunNotFoundError("生成任务不存在")
        if "resume" not in run:
            # A stopped process can leave a run before its first output; resume
            # is explicit and read remains free of model side effects.
            raise RunConflictError("任务尚未生成结果，请调用恢复接口")
        return self._outcome(run_id, run)
