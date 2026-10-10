import type { GenerationUsage } from "@/lib/api-client";

type Props = {
  usage?: GenerationUsage | null;
  traceUrl?: string | null;
  promptVersion?: string | null;
  compact?: boolean;
};

function number(value: number | undefined) {
  return (value ?? 0).toLocaleString("zh-CN");
}

function cost(value: number | null | undefined) {
  return value == null ? "未配置单价" : `$${value.toFixed(4)}`;
}

/** Shared telemetry summary for generated runs and export items. */
export function GenerationMetrics({ usage, traceUrl, promptVersion, compact = false }: Props) {
  if (!usage) return null;
  return (
    <div className={compact ? "mt-2 text-[10px] text-[var(--muted)]" : "rounded-md border border-[var(--border)] bg-[var(--panel-2)] p-3 text-xs"}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span>tokens {number(usage.total_tokens)}（入 {number(usage.input_tokens)} / 出 {number(usage.output_tokens)}）</span>
        <span>耗时 {Math.round(usage.latency_ms ?? 0)} ms</span>
        <span>费用 {cost(usage.cost_usd)}</span>
        {usage.unknown_usage_calls > 0 ? <span className="text-[var(--warn)]">{usage.unknown_usage_calls} 次调用用量未知</span> : null}
        {usage.is_stub ? <span className="text-[var(--warn)]">stub 演示数据</span> : null}
        {promptVersion ? <span>prompt {promptVersion}</span> : null}
        {traceUrl ? <a href={traceUrl} target="_blank" rel="noreferrer" className="underline underline-offset-2">查看 trace</a> : null}
      </div>
    </div>
  );
}
