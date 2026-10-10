"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { TopNav } from "@/components/top-nav";
import { ScoreOptimizer } from "@/components/score-optimizer";
import {
  api,
  ApiError,
  newRequestId,
  type ExperienceRead,
  type GenerateResponse,
} from "@/lib/api-client";

const SAMPLE_JD = `后端开发工程师（校招）— 某互联网公司

岗位职责：
1. 负责公司 AI 应用后端服务的设计、开发与迭代；
2. 参与大模型 agent 流程的编排与工程化落地；
3. 与前端协作完成接口设计，保证服务稳定性与可观测性。

任职要求：
1. 本科及以上学历，计算机相关专业，2027 届毕业生；
2. 熟悉 Python，熟悉 FastAPI / Flask 等至少一种 Web 框架；
3. 熟悉 PostgreSQL / MySQL，了解索引与慢查询优化；
4. 了解 Docker、CI/CD 流程；
5. 有以下经验优先：LangGraph / LangChain 等 agent 编排框架、大模型 API 接入经验、
   可观测性体系（OpenTelemetry / Langfuse）建设经验。

我们希望你：有从 0 到 1 独立交付一个完整项目的经验，遇到问题能自己查文档解决，
对工程质量有要求（写测试、写文档）。`;

type Status = { kind: "idle" } | { kind: "busy" } | { kind: "done" } | { kind: "error"; message: string };
const RUN_STORAGE_KEY = "resume-optimizer:last-generation-run";
const runStatusLabels = { pending: "等待生成", running: "生成中", completed: "已完成", partial: "部分完成", failed: "生成失败" };

function newRunId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 15) | 64;
  bytes[8] = (bytes[8]! & 63) | 128;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function rememberedRun(jdId: string | null, requestedExperiences: Set<string> | null): string | null {
  try {
    const value = localStorage.getItem(RUN_STORAGE_KEY);
    if (!value) return null;
    const saved = JSON.parse(value) as { runId?: unknown; jdId?: unknown; experienceIds?: unknown };
    const sameSelection = requestedExperiences === null ||
      (Array.isArray(saved.experienceIds) && saved.experienceIds.length === requestedExperiences.size &&
        saved.experienceIds.every((id: unknown) => typeof id === "string" && requestedExperiences.has(id)));
    return typeof saved.runId === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(saved.runId) && (!jdId || saved.jdId === jdId) && sameSelection ? saved.runId : null;
  } catch { return null; }
}

function rememberRun(runId: string | null, jdId: string | null = null, experienceIds: string[] = []) {
  try {
    if (runId) localStorage.setItem(RUN_STORAGE_KEY, JSON.stringify({ runId, jdId, experienceIds }));
    else localStorage.removeItem(RUN_STORAGE_KEY);
  } catch { /* Generation also works when browser storage is unavailable. */ }
}

function failureMessage(error: unknown, action: string): string {
  return error instanceof ApiError ? `${action}（${error.status}）：${error.body.slice(0, 300)}` : `${action}：${String(error)}`;
}

function optsFor(controller: AbortController) {
  return { requestId: newRequestId(), signal: controller.signal };
}

export default function GeneratePage() {
  const [experiences, setExperiences] = useState<ExperienceRead[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [jdText, setJdText] = useState("");
  const [jdId, setJdId] = useState<string | null>(null);
  const generationController = useRef<AbortController | null>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [result, setResult] = useState<GenerateResponse | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [activeAction, setActiveAction] = useState<number | "resume" | null>(null);
  const [inputError, setInputError] = useState<string | null>(null);
  const [previewVersion, setPreviewVersion] = useState(0);
  const [scoringBusy, setScoringBusy] = useState(false);

  // URL 预填只在挂载时读取，已有岗位与经历并行加载，卸载时取消请求。
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams(window.location.search);
    const requestedJd = params.get("jd");
    const requestedExperiences = params.has("experiences")
      ? new Set((params.get("experiences") ?? "").split(",").filter(Boolean))
      : null;
    const requestedRun = params.get("run");
    const savedRunId = requestedRun && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(requestedRun)
      ? requestedRun
      : rememberedRun(requestedJd, requestedExperiences);
    if (savedRunId) {
      setRunId(savedRunId);
      setStatus({ kind: "busy" });
      api.getGenerationRun(savedRunId, optsFor(controller))
        .then(data => {
          if (controller.signal.aborted) return;
          setResult(data);
          setStatus({ kind: "done" });
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          if (error instanceof ApiError && error.status === 404) {
            rememberRun(null);
            setRunId(null);
            setStatus({ kind: "idle" });
          } else {
            setStatus({ kind: "error", message: failureMessage(error, "生成记录读取失败") });
          }
        });
    }
    const opts = { requestId: newRequestId(), signal: controller.signal };
    Promise.allSettled([
      api.listExperiences(opts),
      requestedJd ? api.getJob(requestedJd, opts) : Promise.resolve(null),
    ])
      .then(([experienceResult, jobResult]) => {
        if (controller.signal.aborted) return;
        const errors: string[] = [];
        if (experienceResult.status === "fulfilled") {
          const data = experienceResult.value;
          setExperiences(data.items);
          setSelected(new Set(data.items.filter(item => requestedExperiences === null || requestedExperiences.has(item.id)).map(item => item.id)));
        } else {
          setExperiences([]);
          errors.push("素材库读取失败");
        }
        if (jobResult.status === "fulfilled") {
          const job = jobResult.value;
          if (job) {
            setJdText(job.raw_text);
            setJdId(job.id);
          }
        } else {
          const err: unknown = jobResult.reason;
          errors.push(err instanceof ApiError ? `岗位读取失败（${err.status}）：${err.body.slice(0, 300)}` : `岗位读取失败：${String(err)}`);
        }
        if (errors.length > 0) {
          setInputError(errors.join("；"));
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setExperiences([]);
        setInputError(failureMessage(error, "预填读取失败"));
      });
    return () => {
      controller.abort();
      generationController.current?.abort();
    };
  }, []);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const generate = useCallback(async () => {
    if (generationController.current || scoringBusy) return;
    const controller = new AbortController();
    generationController.current = controller;
    const nextRunId = newRunId();
    setRunId(nextRunId);
    rememberRun(nextRunId, jdId, [...selected]);
    setStatus({ kind: "busy" });
    setResult(null);
    try {
      const data = await api.generateResume(
        {
          jd_id: jdId,
          jd_text: jdId ? null : jdText.trim(),
          experience_ids: [...selected],
          run_id: nextRunId,
          // `persist` 在后端有默认值 True，但类型管线对「带 default 字面量的字段」
          // 会生成为**必填**（见 docs/M0-summary.md 的契约 bug 一节）。
          // 这里显式传，而不是在前端加 `?? true` 之类的兜底去掩盖契约定义。
          persist: true,
        },
        { requestId: newRequestId(), signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setResult(data);
      setPreviewVersion(prev => prev + 1);
      setStatus({ kind: "done" });
    } catch (error) {
      if (controller.signal.aborted) return;
      setStatus({
        kind: "error",
        message: failureMessage(error, "生成失败"),
      });
    } finally {
      if (generationController.current === controller) generationController.current = null;
    }
  }, [jdText, jdId, selected, scoringBusy]);

  const continueRun = useCallback(async (index?: number) => {
    if (!runId || status.kind === "busy" || generationController.current || scoringBusy) return;
    const controller = new AbortController();
    generationController.current = controller;
    setActiveAction(index ?? "resume");
    setStatus({ kind: "busy" });
    try {
      const data = index === undefined
        ? await api.resumeGenerationRun(runId, optsFor(controller))
        : await api.retryGenerationItem(runId, index, optsFor(controller));
      if (controller.signal.aborted) return;
      setResult(data);
      setPreviewVersion(prev => prev + 1);
      setStatus({ kind: "done" });
    } catch (error: unknown) {
      if (!controller.signal.aborted) setStatus({ kind: "error", message: failureMessage(error, index === undefined ? "恢复生成失败" : "条目重试失败") });
    } finally {
      if (!controller.signal.aborted) setActiveAction(null);
      if (generationController.current === controller) generationController.current = null;
    }
  }, [runId, status.kind, scoringBusy]);

  const canGenerate = !!(jdId || (jdText.trim().length >= 10 && jdText.trim().length <= 20000)) && selected.size > 0 && status.kind !== "busy" && !scoringBusy && !inputError;

  return (
    <main className="mx-auto w-full max-w-6xl px-6 py-8">
      <TopNav />

      <header className="mb-6">
        <p className="font-mono text-xs tracking-widest text-[var(--muted)] uppercase">定向生成</p>
        <h1 className="mt-2 text-2xl font-semibold">选择经历，生成岗位对应的简历</h1>
        <p className="mt-2 text-sm text-[var(--muted)]">
          使用已保存的岗位画像，或粘贴新的 JD，生成简历并导出 PDF。
        </p>
      </header>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* ------------------------------------------------ 左：输入 */}
        <section className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5">
          {inputError ? <p role="alert" className="mb-3 text-xs text-[var(--err)]">{inputError}</p> : null}
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-mono text-sm">① JD 原文</h2>
            <button
              type="button"
              disabled={experiences === null || status.kind === "busy"}
              onClick={() => { setJdText(SAMPLE_JD); setJdId(null); setInputError(null); }}
              className="rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-1 text-xs hover:bg-[var(--border)]"
            >
              填入示例
            </button>
          </div>
          <textarea
            value={jdText}
            disabled={experiences === null || status.kind === "busy"}
            maxLength={20000}
            onChange={(event) => { setJdText(event.target.value); setJdId(null); setInputError(null); }}
            rows={14}
            placeholder="把招聘网站上的 JD 直接粘进来即可，不用整理格式。"
            className="w-full resize-y rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 font-mono text-xs leading-relaxed outline-none focus:border-[var(--accent)]"
          />
          <p className="mt-2 text-[11px] text-[var(--muted)]">{jdText.trim().length} 字（10–20000 字）{jdId ? " · 使用已保存的岗位画像；编辑原文后会重新解析" : ""}</p>

          <h2 className="mt-6 mb-2 font-mono text-sm">② 选用哪些经历</h2>
          {experiences === null ? (
            <p className="text-xs text-[var(--muted)]">读取素材库…</p>
          ) : experiences.length === 0 ? (
            <p className="rounded-md border border-[var(--warn)]/40 bg-[var(--warn)]/10 px-3 py-2 text-xs text-[var(--warn)]">
              素材库为空。请先到
              <Link href="/library" className="mx-1 underline underline-offset-2">
                素材库
              </Link>
              新增经历。
            </p>
          ) : (
            <ul className="space-y-2">
              {experiences.map((item) => (
                <li key={item.id}>
                  <label className="flex cursor-pointer items-start gap-3 rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2">
                    <input
                      type="checkbox"
                      disabled={status.kind === "busy"}
                      checked={selected.has(item.id)}
                      onChange={() => toggle(item.id)}
                      className="mt-1 accent-[var(--accent)]"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm">
                        {item.org}
                        <span className="text-[var(--muted)]"> · {item.role}</span>
                      </span>
                      <span className="mt-1 block truncate font-mono text-[11px] text-[var(--muted)]">
                        {item.kind} · {(item.skill_tags ?? []).join(" / ") || "无标签"}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}

          <button
            type="button"
            onClick={() => void generate()}
            disabled={!canGenerate}
            className="mt-6 w-full rounded-md px-4 py-2.5 text-sm font-medium transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
            style={{ background: "var(--accent)", color: "#0b0d12" }}
          >
            {status.kind === "busy" ? "生成中…" : "生成简历"}
          </button>
        </section>

        {/* ------------------------------------------------ 右：结果 */}
        <section className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5">
          <h2 className="mb-3 font-mono text-sm">③ 生成结果</h2>

          {status.kind === "idle" ? (
            <p className="text-sm text-[var(--muted)]">还没生成。左边填好 JD 后点「生成简历」。</p>
          ) : null}

          {status.kind === "busy" ? <p role="status" className="text-sm text-[var(--muted)]">调用中，请稍候…</p> : null}

          {status.kind === "error" ? (
            <div className="rounded-md border border-[var(--err)]/40 bg-[var(--err)]/10 px-3 py-3">
              <p className="font-mono text-xs break-all text-[var(--err)]">{status.message}</p>
            </div>
          ) : null}

          {runId && !result && status.kind === "error" ? (
            <button type="button" onClick={() => void continueRun()} className="mt-3 rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2 text-xs">恢复上次生成</button>
          ) : null}
          {result ? <ResultView result={result} onRetry={index => void continueRun(index)} onResume={() => void continueRun()} busy={status.kind === "busy" || scoringBusy} activeAction={activeAction} /> : null}
          {result?.preview_path ? <Link href={`/edit/${result.resume.id}`} className="mt-4 inline-block rounded-md border border-[var(--accent)] bg-[var(--accent)]/10 px-3 py-2 text-xs hover:bg-[var(--accent)]/20">编辑简历</Link> : null}
          {result?.preview_path ? <Link href={`/export?resume=${result.resume.id}`} className="mt-4 ml-2 inline-block rounded-md border border-[var(--border)] px-3 py-2 text-xs">选择模板并导出</Link> : null}
        </section>
      </div>

      {result?.preview_path && result.resume.sections.some(section => section.entries.length > 0) ? <div className="mt-5"><ScoreOptimizer key={result.resume.id} resumeId={result.resume.id} onBusyChange={setScoringBusy} disabledReason={status.kind === "busy" ? "请等待生成完成。" : !result.resume.jd_id ? "这份简历没有绑定已解析岗位，暂时无法评分优化。" : undefined} /></div> : null}

      {/* ------------------------------------------------ 预览 */}
      {result && result.preview_path && result.resume.sections.some(section => section.entries.length > 0) ? (
        <section className="mt-5 rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-mono text-sm">④ 预览（服务端渲染的 HTML，与 PDF 同源）</h2>
            <div className="flex flex-wrap gap-2">
              <Link
                href={`/print/${result.resume.id}`}
                target="_blank"
                className="rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-1.5 text-xs hover:bg-[var(--border)]"
              >
                浏览器打印（另存为 PDF）
              </Link>
              <a
                href={api.resumePdfUrl(result.resume.id, true)}
                className="rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-1.5 text-xs hover:bg-[var(--border)]"
              >
                下载 PDF（服务端导出）
              </a>
            </div>
          </div>
          <iframe
            key={previewVersion}
            title="简历预览"
            src={api.resumeHtmlUrl(result.resume.id)}
            className="h-[900px] w-full rounded-md border border-[var(--border)] bg-white"
          />
        </section>
      ) : null}
    </main>
  );
}

/* ------------------------------------------------------------------ */

function ResultView({ result, onRetry, onResume, busy, activeAction }: {
  result: GenerateResponse;
  onRetry: (index: number) => void;
  onResume: () => void;
  busy: boolean;
  activeAction: number | "resume" | null;
}) {
  const { profile, resume, warnings, failures, items, checkpoint } = result;

  return (
    <div className="space-y-4 text-sm">
      <dl className="space-y-2">
        <Row k="标题" v={resume.title} />
        <Row k="运行状态" v={runStatusLabels[checkpoint.status]} />
        <Row k="运行 ID" v={result.run_id} mono />
        <Row k="产出模型" v={`${result.provider} · ${result.model}`} />
        <Row k="trace_id" v={result.trace_id} mono />
        <Row k="预览地址" v={result.preview_path ?? "—"} mono />
      </dl>

      <div className="space-y-2">
        <p className="font-mono text-xs text-[var(--muted)]">经历处理结果（成功 {items.filter(item => item.status === "succeeded").length} / {items.length}）</p>
        {items.map(item => <div key={item.index} className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2 text-xs">
          <span className="min-w-0 truncate" title={item.org}>{item.org}</span>
          <span className="shrink-0" style={{ color: item.status === "succeeded" ? "var(--ok)" : item.status === "failed" ? "var(--err)" : "var(--muted)" }}>{item.status === "succeeded" ? "已完成" : item.status === "failed" ? "失败" : "待处理"}</span>
        </div>)}
      </div>

      {failures.length > 0 ? <div className="space-y-2" role="status">
        <p className="text-xs font-medium text-[var(--err)]">需要处理的经历</p>
        {failures.map(failure => {
          const item = items.find(value => value.index === failure.index);
          return <div key={failure.index} className="rounded-md border border-[var(--err)]/40 p-3 text-xs">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0"><p className="font-medium">{item?.org || failure.experience_id}</p><p className="mt-1 break-words text-[var(--err)]">{failure.error}</p></div>
              {failure.retryable ? <button type="button" disabled={busy} onClick={() => onRetry(failure.index)} className="shrink-0 rounded-md border border-[var(--border)] px-2 py-1 disabled:opacity-40">{activeAction === failure.index ? "重试中…" : "重试此条"}</button> : null}
            </div>
            {(failure.details ?? []).length > 0 ? <ul className="mt-2 list-disc space-y-1 pl-4 text-[var(--muted)]">{failure.details?.map((detail, index) => <li key={index}>{detail}</li>)}</ul> : null}
          </div>;
        })}
      </div> : null}
      {checkpoint.resumable ? <button type="button" disabled={busy} onClick={onResume} className="rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2 text-xs disabled:opacity-40">{activeAction === "resume" ? "恢复中…" : "继续生成未完成条目"}</button> : null}

      {result.is_stub ? (
        <div className="rounded-md border border-[var(--warn)]/40 bg-[var(--warn)]/10 px-3 py-2">
          <p className="text-xs font-medium text-[var(--warn)]">当前为 stub 桩数据</p>
          <p className="mt-1 text-[11px] text-[var(--muted)]">
            字段值带【fixture】前缀，用于验证工程链路。要看真实改写效果，
            在 apps/api/.env 里配置 LLM_PROVIDER=openai-compatible 与 LLM_API_KEY。
          </p>
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <ul className="space-y-1">
          {warnings.map((warning) => (
            <li
              key={warning}
              className="rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-1.5 text-[11px] text-[var(--muted)]"
            >
              {warning}
            </li>
          ))}
        </ul>
      ) : null}

      <div>
        <p className="mb-2 font-mono text-xs text-[var(--muted)] uppercase">岗位画像</p>
        <div className="space-y-2">
          <Chips label="必备技能" items={profile.required_skills} tone="var(--accent)" />
          <Chips label="加分项" items={profile.nice_to_have} tone="var(--ok)" />
          <Chips label="关键词" items={profile.keywords} tone="var(--muted)" />
        </div>
      </div>

      <div>
        <p className="mb-2 font-mono text-xs text-[var(--muted)] uppercase">
          结构化结果（事实字段来自素材库，不由模型产出）
        </p>
        <div className="space-y-3">
          {resume.sections.map((section) => (
            <div key={section.title}>
              <p className="text-xs font-medium" style={{ color: "var(--accent)" }}>
                {section.title}
              </p>
              {section.entries.map((entry) => (
                <div key={`${entry.org}-${entry.role}`} className="mt-1.5 pl-3">
                  <p className="text-xs">
                    {entry.org}
                    <span className="text-[var(--muted)]"> · {entry.role}</span>
                    {entry.period ? (
                      <span className="ml-2 font-mono text-[11px] text-[var(--muted)]">
                        {entry.period}
                      </span>
                    ) : null}
                  </p>
                  <ul className="mt-1 list-disc space-y-0.5 pl-4">
                    {entry.bullets.map((bullet) => (
                      <li key={bullet.text} className="text-xs">
                        {bullet.text}
                        {bullet.evidence.length > 0 ? (
                          <span
                            className="ml-1 cursor-help text-[10px]"
                            style={{ color: "var(--muted)" }}
                            title={bullet.evidence.join("\n")}
                          >
                            [事实来源]
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Chips({ label, items, tone }: { label: string; items: string[]; tone: string }) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-mono text-[11px] text-[var(--muted)]">{label}</span>
      {items.map((item) => (
        <span
          key={item}
          className="rounded-full border px-2 py-0.5 text-[11px]"
          style={{ borderColor: tone, color: tone }}
        >
          {item}
        </span>
      ))}
    </div>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="shrink-0 font-mono text-xs text-[var(--muted)]">{k}</dt>
      <dd className={`truncate text-right text-xs ${mono ? "font-mono" : ""}`} title={v}>
        {v}
      </dd>
    </div>
  );
}
