"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, newRequestId, type ResumeSection, type ScoreResponse } from "@/lib/api-client";

const buttonClass = "rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2 text-xs disabled:opacity-40";
const storageKey = (id: string) => `resume-optimizer:last-score:${id}`;
const stopLabels: Record<ScoreResponse["loop"]["stop_reason"], string> = {
  threshold: "已达到目标分数",
  max_rounds: "已达到改写轮数上限",
  cost_limit: "调用预算不足，停止继续改写",
  no_low_score_items: "没有需要定向改写的经历",
};
const roundLabel = (round: number) => round === 0 ? "初稿" : `第 ${round} 轮改写`;

function failure(error: unknown): string {
  if (error instanceof ApiError) {
    try { return `评分失败（${error.status}）：${JSON.parse(error.body).detail}`; } catch { return `评分失败（${error.status}）：${error.body.slice(0, 250)}`; }
  }
  return `评分失败：${String(error)}`;
}

export function ScoreOptimizer({ resumeId, disabledReason, onBusyChange }: {
  resumeId: string;
  disabledReason?: string;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [result, setResult] = useState<ScoreResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [threshold, setThreshold] = useState("80");
  const [maxRounds, setMaxRounds] = useState(2);
  const [round, setRound] = useState(0);
  const request = useRef<AbortController | null>(null);
  const busyCallback = useRef(onBusyChange);
  busyCallback.current = onBusyChange;
  const validThreshold = threshold.trim() !== "" && Number.isFinite(Number(threshold)) && Number(threshold) >= 0 && Number(threshold) <= 100;

  useEffect(() => {
    const controller = new AbortController();
    const requestToken = controller;
    request.current = controller;
    setResult(null);
    setError(null);
    setBusy(false);
    busyCallback.current?.(false);
    let saved: string | null = null;
    try { saved = localStorage.getItem(storageKey(resumeId)); } catch { /* Optional history shortcut. */ }
    if (saved && /^[0-9a-f-]{36}$/i.test(saved)) {
      setBusy(true);
      busyCallback.current?.(true);
      api.getScoreRun(resumeId, saved, { signal: controller.signal, requestId: newRequestId() })
        .then(value => {
          if (controller.signal.aborted) return;
          setResult(value);
          setRound(value.loop.best.round);
        })
        .catch((cause: unknown) => {
          if (controller.signal.aborted) return;
          if (cause instanceof ApiError && cause.status === 404) {
            try { localStorage.removeItem(storageKey(resumeId)); } catch { /* Optional history shortcut. */ }
          } else setError(failure(cause));
        })
        .finally(() => {
          if (controller.signal.aborted || request.current !== requestToken) return;
          request.current = null;
          setBusy(false);
          busyCallback.current?.(false);
        });
    } else request.current = null;
    return () => {
      controller.abort();
      request.current?.abort();
      request.current = null;
    };
  }, [resumeId]);

  async function optimize() {
    if (request.current || disabledReason || !validThreshold) return;
    const controller = new AbortController();
    const requestToken = controller;
    request.current = controller;
    setBusy(true);
    busyCallback.current?.(true);
    setError(null);
    try {
      const value = await api.scoreResume(resumeId, { threshold: Number(threshold), max_rounds: maxRounds, cost_limit: 100, persist: true }, { signal: controller.signal, requestId: newRequestId() });
      if (controller.signal.aborted) return;
      setResult(value);
      setRound(value.loop.best.round);
      try { if (value.score_run_id) localStorage.setItem(storageKey(resumeId), value.score_run_id); } catch { /* Results also work without local storage. */ }
    } catch (cause: unknown) {
      if (!controller.signal.aborted) setError(failure(cause));
    } finally {
      if (!controller.signal.aborted && request.current === requestToken) { request.current = null; setBusy(false); busyCallback.current?.(false); }
    }
  }

  const initial = result?.loop.snapshots[0];
  const selected = result?.loop.snapshots.find(snapshot => snapshot.round === round);
  const before = (initial?.sections as ResumeSection[] | undefined)?.flatMap(section => section.entries) ?? [];
  const after = (selected?.sections as ResumeSection[] | undefined)?.flatMap(section => section.entries) ?? [];
  const changed = after.filter((entry, index) => JSON.stringify(entry.bullets.map(bullet => bullet.text)) !== JSON.stringify(before[index]?.bullets.map(bullet => bullet.text))).length;
  const best = result?.loop.best;
  const bestChanged = best && initial ? JSON.stringify(best.sections) !== JSON.stringify(initial.sections) : false;

  return <section className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5">
    <h2 className="text-sm font-semibold">评分并优化</h2>
    <p className="mt-2 text-xs leading-relaxed text-[var(--muted)]">按岗位评分，对低分经历自动改写，最多两轮。每轮结果可对比，最佳版另存为一份简历。</p>
    <div className="mt-4 flex flex-wrap items-end gap-3">
      <label className="text-xs">目标分数<input aria-label="目标分数" type="number" min={0} max={100} step="any" value={threshold} onChange={event => setThreshold(event.target.value)} disabled={busy || !!disabledReason} className="mt-1 block w-24 rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2" /></label>
      <label className="text-xs">最多改写<select aria-label="最多改写" value={maxRounds} onChange={event => setMaxRounds(Number(event.target.value))} disabled={busy || !!disabledReason} className="mt-1 block rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2"><option value={0}>仅评分</option><option value={1}>1 轮</option><option value={2}>2 轮</option></select></label>
      <button className={buttonClass} onClick={() => void optimize()} disabled={busy || !!disabledReason || !validThreshold}>{busy ? "评分与优化中…" : result ? "重新评分并优化" : "评分并优化"}</button>
    </div>
    {disabledReason ? <p className="mt-2 text-xs text-[var(--warn)]">{disabledReason}</p> : null}
    {busy ? <p role="status" className="mt-3 text-xs text-[var(--muted)]">正在读取评分或执行优化，完成后展示各轮结果。请保持页面打开。</p> : null}
    {error ? <p role="alert" className="mt-3 text-xs text-[var(--err)]">{error}</p> : null}
    {error && result ? <p className="mt-2 text-xs text-[var(--warn)]">以下保留的是上次成功评分的结果。</p> : null}
    {result && initial && best && selected ? <div className="mt-5 space-y-4">
      <div className="rounded-md bg-[var(--panel-2)] p-3">
        <p className="text-sm">初稿 {initial.result.score.toFixed(1)} → 最佳 {best.result.score.toFixed(1)} 分 · {roundLabel(best.round)}</p>
        <p className="mt-1 text-xs text-[var(--muted)]">{stopLabels[result.loop.stop_reason]} · 已执行 {result.loop.snapshots.length - 1} 轮改写</p>
        {!bestChanged ? <p className="mt-2 text-xs text-[var(--warn)]">{result.loop.snapshots.length === 1 ? "本次未发生改写。初稿已达标、仅评分、预算不足或没有低分条目时会直接停止。" : "改写未带来更高的可用评分，最佳版保留初稿内容。可切换轮次查看改写尝试。"}</p> : null}
        {result.loop.snapshots.some(snapshot => snapshot.result.is_stub) ? <p className="mt-2 text-xs text-[var(--warn)]">当前使用演示评分规则，分数不代表真实模型评估质量。</p> : null}
        {result.resume.generator?.startsWith("stub:") ? <p className="mt-2 text-xs text-[var(--warn)]">当前最佳稿来自演示生成器，文字变化可能很少。</p> : null}
        <p className="mt-2 text-[11px] text-[var(--muted)]">以下是本次评分的历史快照。编辑器和 PDF 打开的是最佳版简历的当前内容；后续编辑不会改变这些评分快照。</p>
      </div>
      <div className="flex flex-wrap gap-2" role="group" aria-label="评分轮次">{result.loop.snapshots.map(snapshot => <button key={snapshot.round} className={buttonClass} aria-pressed={round === snapshot.round} style={round === snapshot.round ? { borderColor: "var(--accent)", color: "var(--accent)" } : undefined} onClick={() => setRound(snapshot.round)}>{roundLabel(snapshot.round)} · {snapshot.result.score.toFixed(1)} 分{best.round === snapshot.round ? " · 最佳" : ""}</button>)}</div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{selected.result.dimensions.map(dimension => <div key={dimension.key} className="rounded-md border border-[var(--border)] p-3"><p className="text-xs text-[var(--muted)]">{dimension.label}</p><p className="mt-1 text-lg">{dimension.score.toFixed(1)}</p><p className="mt-1 text-[11px] text-[var(--muted)]">{dimension.rationale}</p></div>)}</div>
      {selected.result.deductions.length ? <details className="text-xs"><summary className="cursor-pointer">扣分原因与改进建议（{selected.result.deductions.length}）</summary><ul className="mt-2 space-y-2">{selected.result.deductions.map((item, index) => <li key={index}>经历 {item.item_index + 1} · {item.reason}<p className="mt-1 text-[var(--muted)]">建议：{item.suggestion}</p></li>)}</ul></details> : null}
      {selected.result.recommendations.map((recommendation, index) => <p className="text-xs text-[var(--muted)]" key={index}>{recommendation}</p>)}
      {selected.result.factual_violations?.length ? <p className="text-xs text-[var(--err)]">本轮有事实核验失败的经历，不能作为最佳版本导出。</p> : null}
      {selected.result.warnings?.map((warning, index) => <p key={index} className="text-xs text-[var(--warn)]">{warning}</p>)}
      <h3 className="text-sm font-medium">初稿与{roundLabel(round)}对比 · {changed} 段经历文字有变化</h3>
      <div className="space-y-3">{after.map((entry, index) => {
        const original = before[index];
        const same = JSON.stringify(original?.bullets.map(bullet => bullet.text)) === JSON.stringify(entry.bullets.map(bullet => bullet.text));
        return <div key={index} className="rounded-md border border-[var(--border)] p-3"><p className="mb-3 text-xs font-medium">{entry.org} · {entry.role} · {same ? "文字未变化" : "已改写"}</p><div className="grid gap-3 sm:grid-cols-2">{[{ label: "初稿", bullets: original?.bullets ?? [] }, { label: roundLabel(round), bullets: entry.bullets }].map((column, ci) => <div key={ci} className="rounded-md bg-[var(--panel-2)] p-3"><p className="mb-2 text-[11px] text-[var(--muted)]">{column.label}</p><ul className="list-disc space-y-2 pl-4 text-xs leading-relaxed">{column.bullets.map((bullet, bi) => <li key={bi}>{bullet.text}</li>)}</ul></div>)}</div></div>;
      })}</div>
      <div className="flex flex-wrap gap-2"><Link className={buttonClass} href={`/edit/${result.resume.id}`}>打开最佳版编辑器</Link><a className={buttonClass} href={api.resumePdfUrl(result.resume.id, true)}>下载最佳版当前 PDF</a></div>
    </div> : null}
  </section>;
}
