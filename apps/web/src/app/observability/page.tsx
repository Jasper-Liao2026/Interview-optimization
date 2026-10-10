"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { GenerationMetrics } from "@/components/generation-metrics";
import { TopNav } from "@/components/top-nav";
import { api, ApiError, newRequestId, type ObservabilityUsage } from "@/lib/api-client";

function errorText(error: unknown) {
  return error instanceof ApiError ? `读取观测数据失败（${error.status}）：${error.body.slice(0, 300)}` : `读取观测数据失败：${String(error)}`;
}

export default function ObservabilityPage() {
  const [data, setData] = useState<ObservabilityUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const active = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    setError(null);
    try {
      const result = await api.getObservabilityUsage({ requestId: newRequestId(), signal: controller.signal });
      if (!controller.signal.aborted) setData(result);
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorText(cause));
    } finally {
      if (active.current === controller) {
        active.current = null;
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void load();
    return () => { active.current?.abort(); active.current = null; };
  }, [load]);

  return (
    <main className="mx-auto w-full max-w-7xl px-6 py-8">
      <TopNav />
      <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="font-mono text-xs uppercase tracking-widest text-[var(--muted)]">运行观测</p>
          <h1 className="mt-2 text-2xl font-semibold">Token、耗时与调用追踪</h1>
          <p className="mt-2 text-sm text-[var(--muted)]">查看最近生成任务的汇总，并跳转回任务或 Langfuse trace。</p>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading} className="rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2 text-xs disabled:opacity-40">
          {loading ? "读取中…" : "刷新"}
        </button>
      </header>
      {error ? <p role="alert" className="mb-4 break-words rounded-md border border-[var(--err)] p-3 text-xs text-[var(--err)]">{error}</p> : null}
      {data ? <>
        <section className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Summary label="总 token" value={(data.total_tokens ?? 0).toLocaleString("zh-CN")} />
          <Summary label="输入 token" value={(data.input_tokens ?? 0).toLocaleString("zh-CN")} />
          <Summary label="输出 token" value={(data.output_tokens ?? 0).toLocaleString("zh-CN")} />
          <Summary label="总耗时" value={`${Math.round(data.latency_ms ?? 0).toLocaleString("zh-CN")} ms`} />
          <Summary label="费用" value={data.cost_usd == null ? "未配置单价" : `$${data.cost_usd.toFixed(4)}`} />
        </section>
        <p className="mb-3 text-xs text-[var(--muted)]">最近 {data.runs?.length ?? 0} 个任务 · 用量未知调用 {data.unknown_usage_calls ?? 0} 次</p>
        <section className="space-y-3">
          {(data.runs ?? []).map(run => <article key={run.run_id} className="rounded-md border border-[var(--border)] bg-[var(--panel)] p-4">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="font-mono break-all">{run.run_id}</span>
              <span className="text-[var(--muted)]">{run.status || "未知状态"} · {run.updated_at ? new Date(run.updated_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "时间未知"}</span>
            </div>
            <GenerationMetrics usage={run.usage} traceUrl={run.langfuse_trace_url} promptVersion={run.usage?.calls?.[0]?.prompt_version} />
            <div className="mt-3 flex flex-wrap gap-3 text-xs">
              <Link href={`/generate?run=${run.run_id}`} className="underline underline-offset-2">打开生成任务</Link>
              {run.trace_id && !run.langfuse_trace_url ? <span className="font-mono text-[var(--muted)]">trace {run.trace_id}</span> : null}
            </div>
          </article>)}
          {!data.runs?.length ? <p className="rounded-md border border-[var(--border)] p-5 text-sm text-[var(--muted)]">暂无生成任务。</p> : null}
        </section>
      </> : null}
    </main>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return <div className="rounded-md border border-[var(--border)] bg-[var(--panel)] p-4"><p className="text-[11px] text-[var(--muted)]">{label}</p><p className="mt-2 break-words text-lg font-semibold">{value}</p></div>;
}
