"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { TopNav } from "@/components/top-nav";
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

export default function GeneratePage() {
  const [experiences, setExperiences] = useState<ExperienceRead[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [jdText, setJdText] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [result, setResult] = useState<GenerateResponse | null>(null);

  // 素材库：M1 没有录入 UI（M2-4 才有），这里只负责勾选
  useEffect(() => {
    const controller = new AbortController();
    api
      .listExperiences({ requestId: newRequestId(), signal: controller.signal })
      .then((data) => {
        setExperiences(data.items);
        // 默认全选：M1 的场景就是「用我全部经历针对这个 JD 生成一份」
        setSelected(new Set(data.items.map((item) => item.id)));
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setExperiences([]);
        setStatus({
          kind: "error",
          message: error instanceof ApiError ? `素材库读取失败：${error.status}` : String(error),
        });
      });
    return () => controller.abort();
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
    setStatus({ kind: "busy" });
    setResult(null);
    try {
      const data = await api.generateResume(
        {
          jd_text: jdText,
          experience_ids: [...selected],
          // `persist` 在后端有默认值 True，但类型管线对「带 default 字面量的字段」
          // 会生成为**必填**（见 docs/M0-summary.md 的契约 bug 一节）。
          // 这里显式传，而不是在前端加 `?? true` 之类的兜底去掩盖契约定义。
          persist: true,
        },
        { requestId: newRequestId() },
      );
      setResult(data);
      setStatus({ kind: "done" });
    } catch (error) {
      setStatus({
        kind: "error",
        message:
          error instanceof ApiError
            ? `生成失败（${error.status}）：${error.body.slice(0, 300)}`
            : String(error),
      });
    }
  }, [jdText, selected]);

  const canGenerate = jdText.trim().length >= 10 && selected.size > 0 && status.kind !== "busy";

  return (
    <main className="mx-auto w-full max-w-6xl px-6 py-8">
      <TopNav />

      <header className="mb-6">
        <p className="font-mono text-xs tracking-widest text-[var(--muted)] uppercase">M1 · 垂直切片</p>
        <h1 className="mt-2 text-2xl font-semibold">粘贴 JD，生成一份可下载的 PDF</h1>
        <p className="mt-2 text-sm text-[var(--muted)]">
          一条经历 + 一个 JD → 解析 → 改写 → 渲染 → 导出。这是最小但完整的那条链路。
        </p>
      </header>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* ------------------------------------------------ 左：输入 */}
        <section className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-mono text-sm">① JD 原文</h2>
            <button
              type="button"
              onClick={() => setJdText(SAMPLE_JD)}
              className="rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-1 text-xs hover:bg-[var(--border)]"
            >
              填入示例
            </button>
          </div>
          <textarea
            value={jdText}
            onChange={(event) => setJdText(event.target.value)}
            rows={14}
            placeholder="把招聘网站上的 JD 直接粘进来即可，不用整理格式。"
            className="w-full resize-y rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 font-mono text-xs leading-relaxed outline-none focus:border-[var(--accent)]"
          />
          <p className="mt-2 text-[11px] text-[var(--muted)]">{jdText.trim().length} 字（≥ 10 字）</p>

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

          {status.kind === "busy" ? <p className="text-sm text-[var(--muted)]">调用中，请稍候…</p> : null}

          {status.kind === "error" ? (
            <div className="rounded-md border border-[var(--err)]/40 bg-[var(--err)]/10 px-3 py-3">
              <p className="font-mono text-xs break-all text-[var(--err)]">{status.message}</p>
            </div>
          ) : null}

          {result ? <ResultView result={result} /> : null}
        </section>
      </div>

      {/* ------------------------------------------------ 预览 */}
      {result ? (
        <section className="mt-5 rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-mono text-sm">④ 预览（服务端渲染的 HTML，与 PDF 同源）</h2>
            <div className="flex gap-2">
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

function ResultView({ result }: { result: GenerateResponse }) {
  const { profile, resume, warnings } = result;

  return (
    <div className="space-y-4 text-sm">
      <dl className="space-y-2">
        <Row k="标题" v={resume.title} />
        <Row k="产出模型" v={`${result.provider} · ${result.model}`} />
        <Row k="trace_id" v={result.trace_id} mono />
        <Row k="预览地址" v={result.preview_path ?? "—"} mono />
      </dl>

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
