"""Structured revisions and facts-checked AI proposals applied only after confirmation."""

import copy
from uuid import UUID, uuid4

from langgraph.types import Command

from app.agents.checkpoint import GenerationRunStore, RunConflictError
from app.agents.editing_graph import editing_graph
from app.agents.rewriter import ExperienceRewriter
from app.config import Settings
from app.repositories import ExperienceRepository, JobDescriptionRepository
from app.repositories.editing_repo import EditingNotFoundError, EditingRepository, draft_of
from app.schemas.editing import AiEditRequest, AiEditResponse, EditorResponse


class ResumeEditingService:
    def __init__(
        self,
        repository: EditingRepository,
        experiences: ExperienceRepository,
        jobs: JobDescriptionRepository,
        rewriter: ExperienceRewriter,
        runs: GenerationRunStore,
        settings: Settings,
    ) -> None:
        self.repository, self.experiences, self.jobs = repository, experiences, jobs
        self.rewriter, self.runs, self.settings = rewriter, runs, settings

    async def read(self, user_id: UUID, resume_id: UUID) -> EditorResponse:
        return EditorResponse.model_validate(await self.repository.read(user_id, resume_id))

    async def restore(
        self, user_id: UUID, resume_id: UUID, revision_id: UUID, expected_revision: int
    ) -> EditorResponse:
        revision = await self.repository.revision(user_id, resume_id, revision_id)
        await self.repository.save(
            user_id,
            resume_id,
            expected_revision,
            revision["draft"],
            f"回退到版本 {revision['revision']}",
            revision["provenance"],
        )
        return await self.read(user_id, resume_id)

    async def propose(
        self, user_id: UUID, resume_id: UUID, request: AiEditRequest
    ) -> AiEditResponse:
        editor = await self.read(user_id, resume_id)
        if editor.revision != request.expected_revision:
            raise RunConflictError("简历已修改，请刷新并保存后再进行 AI 微调")
        resume = editor.resume
        try:
            entry = resume.sections[request.section_index].entries[request.entry_index]
        except IndexError as exc:
            raise ValueError("选择的经历不存在") from exc
        if not resume.jd_id:
            raise ValueError("AI 微调需要简历绑定已解析的 JD")
        jd = await self.jobs.get(user_id, resume.jd_id)
        if not jd or not jd.get("parsed"):
            raise ValueError("岗位画像不存在或尚未解析")
        if not entry.experience_id:
            raise ValueError("当前经历没有原始素材，无法校验 AI 改写")
        sources = await self.experiences.list_by_ids(user_id, [entry.experience_id])
        if not sources:
            raise ValueError("原始素材已删除，无法校验 AI 改写")
        # Dates and UUIDs must survive the Postgres checkpointer's JSON transport.
        from app.schemas.experience import ExperienceRead

        source = ExperienceRead.model_validate(sources[0]).model_dump(mode="json")
        run_id = uuid4()
        config = {"configurable": {"thread_id": f"edit:{run_id}"}}
        async with self.runs.lock(run_id), self.runs.checkpointer() as saver:
            graph = editing_graph(self.rewriter, saver)
            result = await graph.ainvoke(
                {
                    "source": source,
                    "profile": jd["parsed"],
                    "instruction": request.instruction,
                    "original_bullets": [
                        bullet.model_dump(mode="json") for bullet in entry.bullets
                    ],
                },
                config,
            )
            payload = {
                "run_id": str(run_id),
                "resume_id": str(resume_id),
                "status": "pending",
                "base_revision": editor.revision,
                "section_index": request.section_index,
                "entry_index": request.entry_index,
                "instruction": request.instruction,
                "original_bullets": result["original_bullets"],
                "proposed_bullets": result["proposed_bullets"],
                "warnings": result["warnings"],
                "is_stub": self.settings.llm_provider == "stub",
                "draft": draft_of(resume.model_dump(mode="json")),
                "provenance": {
                    "generator": f"{self.settings.llm_provider}:{self.settings.llm_model}",
                    "generator_vendor": (
                        resume.generator_vendor
                        if resume.generator_vendor == self.settings.generation_vendor
                        else None
                    ),
                },
            }
            if payload["is_stub"]:
                payload["provenance"] = {
                    "generator": "stub:local-edit",
                    "generator_vendor": "stub" if resume.generator_vendor == "stub" else None,
                }
            await self.repository.save_run(user_id, resume_id, run_id, payload)
        return AiEditResponse.model_validate(payload)

    async def read_proposal(self, user_id: UUID, resume_id: UUID, run_id: UUID) -> AiEditResponse:
        payload = await self.repository.get_run(user_id, resume_id, run_id)
        if payload is None:
            raise EditingNotFoundError("AI 微调任务不存在")
        return AiEditResponse.model_validate(payload)

    async def decide(
        self, user_id: UUID, resume_id: UUID, run_id: UUID, accept: bool
    ) -> AiEditResponse:
        async with self.runs.lock(run_id):
            payload = await self.repository.get_run(user_id, resume_id, run_id)
            if payload is None:
                raise EditingNotFoundError("AI 微调任务不存在")
            if payload["status"] != "pending":
                if accept != (payload["status"] == "applied"):
                    raise RunConflictError("该微调任务已有确认结果")
                response = AiEditResponse.model_validate(payload)
                response.editor = await self.read(user_id, resume_id)
                return response
            if accept:
                editor = await self.read(user_id, resume_id)
                if editor.revision != payload["base_revision"]:
                    raise RunConflictError("提案生成后简历已修改，请拒绝该提案并重新微调")
            config = {"configurable": {"thread_id": f"edit:{run_id}"}}
            async with self.runs.checkpointer() as saver:
                graph = editing_graph(self.rewriter, saver)
                state = await graph.aget_state(config)
                if state.next:
                    await graph.ainvoke(Command(resume=accept), config)
                elif not state.values:
                    raise RunConflictError("微调 checkpoint 缺失，请重新生成提案")
            if accept:
                draft = copy.deepcopy(payload["draft"])
                draft["sections"][payload["section_index"]]["entries"][payload["entry_index"]][
                    "bullets"
                ] = payload["proposed_bullets"]
                payload["status"] = "applied"
                await self.repository.save(
                    user_id,
                    resume_id,
                    payload["base_revision"],
                    draft,
                    "AI 微调（人工确认）",
                    payload["provenance"],
                    run_id=run_id,
                    run_payload=payload,
                )
            else:
                payload["status"] = "rejected"
                await self.repository.save_run(user_id, resume_id, run_id, payload)
            response = AiEditResponse.model_validate(payload)
            response.editor = await self.read(user_id, resume_id)
            return response
