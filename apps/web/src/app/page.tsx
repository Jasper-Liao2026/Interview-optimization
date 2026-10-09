"use client";

import { useCallback, useEffect, useState } from "react";

import {
  api,
  ApiError,
  newRequestId,
  type HealthResponse,
  type SystemInfoResponse,
} from "@/lib/api-client";
import { env } from "@/lib/env";
import { TopNav } from "@/components/top-nav";

type LoadState<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T; traceId: string; ms: number }
  | { kind: "error"; message: string; hint: string };

const POLL_INTERVAL_MS = 10_000;

export default function Page() {
  const [health, setHealth] = useState<LoadState<HealthResponse>>({ kind: "loading" });
  const [info, setInfo] = useState<LoadState<SystemInfoResponse>>({ kind: "loading" });
  const [lastChecked, setLastChecked] = useState<string>("");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = useCallback(async () => {
    const traceId = newRequestId();

    setHealth({ kind: "loading" });
    const t0 = performance.now();
    try {
      const data = await api.health({ requestId: traceId });
      setHealth({ kind: "ok", data, traceId, ms: Math.round(performance.now() - t0) });
    } catch (error) {
      setHealth({
        kind: "error",
        message: error instanceof ApiError ? `${error.status} ${error.path}` : String(error),
        hint: "后端没起来。执行 pnpm api:dev，或 docker compose up api。",
      });
    }

    setInfo({ kind: "loading" });
    const t1 = performance.now();
    try {
      const data = await api.systemInfo({ requestId: traceId });
      setInfo({ kind: "ok", data, traceId, ms: Math.round(performance.now() - t1) });
    } catch (error) {
      setInfo({
        kind: "error",
        message: error instanceof ApiError ? `${error.status} ${error.path}` : String(error),
        hint: "接口未注册或 500。看 api 容器日志：docker compose logs -f api。",
      });
    }

    setLastChecked(new Date().toLocaleTimeString("zh-CN"));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [autoRefresh, load]);

  const dbConnected =
    info.kind === "ok" ? info.data.database.connected : null;
  const overall =
    health.kind === "loading" || info.kind === "loading"
      ? "checking"
      : health.kind === "error"
        ? "down"
        : dbConnected === false
          ? "degraded"
          : "up";

  return (
    <main className="mx-auto w-full max-w-5xl px-6 py-10">
      <TopNav />

      <Header
        overall={overall}
        lastChecked={lastChecked}
        autoRefresh={autoRefresh}
        onToggleAutoRefresh={() => setAutoRefresh((v) => !v)}
        onRefresh={() => void load()}
      />

      <ChainStrip overall={overall} dbConnected={dbConnected} />

      <section className="mt-8 grid gap-5 lg:grid-cols-2">
        <Card title="① /api/v1/health" subtitle="后端进程自检">
          <StateView state={health} render={(d) => (
            <dl className="space-y-2 text-sm">
              <Row k="status" v={d.status} tone="ok" />
              <Row k="service" v={d.service} />
              <Row k="version" v={d.version} />
              <Row k="environment" v={d.environment} />
              <Row k="uptime" v={`${d.uptime_seconds.toFixed(1)} s`} />
              <Row k="checked_at" v={d.checked_at} />
              <Row k="trace_id" v={d.trace_id} mono />
            </dl>
          )} />
        </Card>

        <Card title="② /api/v1/system/info" subtitle="穿透到 Postgres 的读库校验">
          <StateView state={info} render={(d) => (
            <div className="space-y-4 text-sm">
              <dl className="space-y-2">
                <Row
                  k="database.connected"
                  v={String(d.database.connected)}
                  tone={d.database.connected ? "ok" : "err"}
                />
                <Row
                  k="database.latency"
                  v={d.database.latency_ms == null ? "—" : `${d.database.latency_ms.toFixed(1)} ms`}
                />
                <Row k="database.version" v={d.database.server_version ?? "—"} />
                {d.database.error ? <Row k="database.error" v={d.database.error} tone="err" /> : null}
                <Row k="milestone" v={d.milestone} />
                <Row k="trace_id" v={d.trace_id} mono />
              </dl>

              <div>
                <p className="mb-2 text-xs font-medium tracking-wide text-[var(--muted)] uppercase">
                  service_meta（来自 migration 落库的数据）
                </p>
                {d.meta.length === 0 ? (
                  <p className="rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2 text-xs text-[var(--muted)]">
                    表为空。执行 pnpm db:up 让 migration 与 seed 生效。
                  </p>
                ) : (
                  <ul className="divide-y divide-[var(--border)] overflow-hidden rounded-md border border-[var(--border)]">
                    {d.meta.map((entry) => (
                      <li key={entry.key} className="flex items-center justify-between gap-3 px-3 py-2">
                        <span className="font-mono text-xs text-[var(--muted)]">{entry.key}</span>
                        <span className="truncate text-xs">{entry.value}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )} />
        </Card>
      </section>

      <Footer apiBaseUrl={env.apiBaseUrl} />
    </main>
  );
}

/* ------------------------------------------------------------------ */

type Overall = "checking" | "up" | "degraded" | "down";

const OVERALL_LABEL: Record<Overall, { label: string; color: string }> = {
  checking: { label: "检测中", color: "var(--muted)" },
  up: { label: "全链路正常", color: "var(--ok)" },
  degraded: { label: "后端在线 · 数据库未通", color: "var(--warn)" },
  down: { label: "后端不可达", color: "var(--err)" },
};

function Header({
  overall,
  lastChecked,
  autoRefresh,
  onToggleAutoRefresh,
  onRefresh,
}: {
  overall: Overall;
  lastChecked: string;
  autoRefresh: boolean;
  onToggleAutoRefresh: () => void;
  onRefresh: () => void;
}) {
  const { label, color } = OVERALL_LABEL[overall];
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <p className="font-mono text-xs tracking-widest text-[var(--muted)] uppercase">
          M0 · 脚手架自检台
        </p>
        <h1 className="mt-2 text-2xl font-semibold">{env.appName}</h1>
        <p className="mt-2 flex items-center gap-2 text-sm">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />
          <span style={{ color }}>{label}</span>
          {lastChecked ? (
            <span className="text-[var(--muted)]">· {lastChecked}</span>
          ) : null}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onToggleAutoRefresh}
          className="rounded-md border border-[var(--border)] bg-[var(--panel)] px-3 py-1.5 text-xs hover:bg-[var(--panel-2)]"
        >
          自动刷新：{autoRefresh ? "开" : "关"}
        </button>
        <button
          type="button"
          onClick={onRefresh}
          className="rounded-md border border-[var(--border)] bg-[var(--panel)] px-3 py-1.5 text-xs hover:bg-[var(--panel-2)]"
        >
          立即检测
        </button>
      </div>
    </header>
  );
}

function ChainStrip({ overall, dbConnected }: { overall: Overall; dbConnected: boolean | null }) {
  const nodes = [
    { name: "Next.js", note: "apps/web", ok: true as boolean | null },
    { name: "FastAPI", note: "apps/api", ok: overall === "checking" ? null : overall !== "down" },
    { name: "Postgres", note: "pgvector/pg16", ok: dbConnected },
  ];

  return (
    <div className="mt-8 flex flex-wrap items-stretch gap-3">
      {nodes.map((node, index) => (
        <div key={node.name} className="flex flex-1 items-center gap-3">
          <div className="min-w-0 flex-1 rounded-lg border border-[var(--border)] bg-[var(--panel)] px-4 py-3">
            <div className="flex items-center gap-2">
              <span
                className="inline-block h-2 w-2 shrink-0 rounded-full"
                style={{
                  background:
                    node.ok === null ? "var(--muted)" : node.ok ? "var(--ok)" : "var(--err)",
                }}
              />
              <span className="truncate text-sm font-medium">{node.name}</span>
            </div>
            <p className="mt-1 font-mono text-[11px] text-[var(--muted)]">{node.note}</p>
          </div>
          {index < nodes.length - 1 ? (
            <span className="text-[var(--muted)]" aria-hidden>
              →
            </span>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function Card({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5">
      <h2 className="font-mono text-sm">{title}</h2>
      <p className="mt-1 mb-4 text-xs text-[var(--muted)]">{subtitle}</p>
      {children}
    </div>
  );
}

function StateView<T>({
  state,
  render,
}: {
  state: LoadState<T>;
  render: (data: T) => React.ReactNode;
}) {
  if (state.kind === "loading") {
    return <p className="text-sm text-[var(--muted)]">请求中…</p>;
  }
  if (state.kind === "error") {
    return (
      <div className="rounded-md border border-[var(--err)]/40 bg-[var(--err)]/10 px-3 py-3">
        <p className="font-mono text-xs text-[var(--err)]">{state.message}</p>
        <p className="mt-2 text-xs text-[var(--muted)]">{state.hint}</p>
      </div>
    );
  }
  return (
    <div>
      {render(state.data)}
      <p className="mt-3 text-[11px] text-[var(--muted)]">
        客户端耗时 {state.ms} ms · 浏览器直连后端（未经过 Next.js 转发）
      </p>
    </div>
  );
}

function Row({ k, v, tone, mono }: { k: string; v: string; tone?: "ok" | "err"; mono?: boolean }) {
  const color = tone === "ok" ? "var(--ok)" : tone === "err" ? "var(--err)" : "var(--text)";
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="shrink-0 font-mono text-xs text-[var(--muted)]">{k}</dt>
      <dd
        className={`truncate text-right text-xs ${mono ? "font-mono" : ""}`}
        style={{ color }}
        title={v}
      >
        {v}
      </dd>
    </div>
  );
}

function Footer({ apiBaseUrl }: { apiBaseUrl: string }) {
  return (
    <footer className="mt-10 border-t border-[var(--border)] pt-4 text-xs text-[var(--muted)]">
      <p>
        后端基址 <span className="font-mono">{apiBaseUrl}</span>
        {apiBaseUrl.includes("localhost") ? (
          <> — 开发期沿用 <span className="font-mono">localhost</span>；如需换成容器内地址，改 apps/web/.env.local。</>
        ) : null}
      </p>
      <p className="mt-1">
        缺 M0-8（Langfuse）与 M1（垂直切片）：本页只证明地基可用，不涉及业务逻辑。
      </p>
    </footer>
  );
}
