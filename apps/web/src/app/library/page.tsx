"use client";

import { useCallback, useEffect, useState } from "react";

import { TopNav } from "@/components/top-nav";
import {
  api,
  ApiError,
  newRequestId,
  type ExperienceCreate,
  type ExperienceRead,
} from "@/lib/api-client";

/* ------------------------------------------------------------------ *
 * 工具函数
 * ------------------------------------------------------------------ */

/** YYYY-MM-DD → YYYY.MM；非法或为空返回 null */
function formatMonth(date?: string | null): string | null {
  if (!date) return null;
  const m = /^(\d{4})-(\d{2})/.exec(date);
  if (!m) return null;
  return `${m[1]}.${m[2]}`;
}

/** 经历时间区间：2026.06 – 2026.08；end 为空 → 2026.09 – 至今；两个都空 → null */
function formatPeriod(start?: string | null, end?: string | null): string | null {
  const s = formatMonth(start);
  const e = formatMonth(end);
  if (!s && !e) return null;
  if (s && !e) return `${s} – 至今`;
  if (!s && e) return e;
  return `${s} – ${e}`;
}

/** 前端固定分组顺序（与后端返回 kind 的字典序不一致，必须前端分组） */
const GROUP_ORDER: { kind: ExperienceRead["kind"]; title: string }[] = [
  { kind: "internship", title: "实习经历" },
  { kind: "project", title: "项目经历" },
  { kind: "campus", title: "校园经历" },
];

const inputCls =
  "w-full rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent)]";
const labelCls = "mb-1 block font-mono text-xs text-[var(--muted)]";
const secondaryBtn =
  "rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-1.5 text-xs transition-colors hover:bg-[var(--border)]";

/* ------------------------------------------------------------------ *
 * 列表卡片
 * ------------------------------------------------------------------ */

function ExperienceCard({
  item,
  onEdit,
  onDelete,
}: {
  item: ExperienceRead;
  onEdit: (item: ExperienceRead) => void;
  onDelete: (item: ExperienceRead) => void;
}) {
  const period = formatPeriod(item.start_date, item.end_date);

  return (
    <article className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium">
            {item.org}
            <span className="text-[var(--muted)]"> · {item.role}</span>
          </p>
          {period ? (
            <p className="mt-1 font-mono text-[11px] text-[var(--muted)]">{period}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button type="button" onClick={() => onEdit(item)} className={secondaryBtn}>
            编辑
          </button>
          <button
            type="button"
            onClick={() => onDelete(item)}
            className="rounded-md border border-[var(--err)]/40 bg-[var(--err)]/10 px-3 py-1.5 text-xs text-[var(--err)] transition-colors hover:bg-[var(--err)]/20"
          >
            删除
          </button>
        </div>
      </div>

      {item.skill_tags && item.skill_tags.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {item.skill_tags.map((tag) => (
            <span
              key={tag}
              className="rounded-full border border-[var(--border)] bg-[var(--panel-2)] px-2 py-0.5 text-[11px] text-[var(--muted)]"
            >
              {tag}
            </span>
          ))}
        </div>
      ) : null}

      {item.highlights && item.highlights.length > 0 ? (
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
          {item.highlights.map((h, i) => (
            <li key={i}>{h}</li>
          ))}
        </ul>
      ) : null}

      {item.metrics && item.metrics.length > 0 ? (
        <div className="mt-3 space-y-1 border-l-2 border-[var(--accent)] pl-3">
          {item.metrics.map((m, i) => (
            <p key={i} className="font-mono text-[11px]">
              <span className="text-[var(--text)]">
                {m.name}：{m.value}
              </span>
              {m.context ? (
                <span className="text-[var(--muted)]">（口径：{m.context}）</span>
              ) : null}
            </p>
          ))}
        </div>
      ) : null}

      {item.variants && item.variants.length > 0 ? (
        <div className="mt-3 space-y-2">
          {item.variants.map((v, i) => (
            <div
              key={i}
              className="rounded-md border border-[var(--border)] bg-[var(--panel-2)] p-3"
            >
              <p className="mb-1 font-mono text-[11px]" style={{ color: "var(--accent)" }}>
                方向：{v.direction}
              </p>
              <p className="whitespace-pre-wrap text-sm">{v.text}</p>
              {v.note ? (
                <p className="mt-1 text-[11px] text-[var(--muted)]">备注：{v.note}</p>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </article>
  );
}

/* ------------------------------------------------------------------ *
 * 表单（新增 / 编辑共用）
 * ------------------------------------------------------------------ */

type DraftMetric = { name: string; value: string; context: string };
type DraftVariant = { direction: string; text: string; note: string };

function ExperienceForm({
  initial,
  onCancel,
  onSaved,
}: {
  initial: ExperienceRead | null;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const isEdit = initial !== null;

  const [kind, setKind] = useState<ExperienceCreate["kind"]>(initial?.kind ?? "project");
  const [org, setOrg] = useState(initial?.org ?? "");
  const [role, setRole] = useState(initial?.role ?? "");
  const [startDate, setStartDate] = useState(initial?.start_date ?? "");
  const [endDate, setEndDate] = useState(initial?.end_date ?? "");
  const [rawDescription, setRawDescription] = useState(initial?.raw_description ?? "");
  const [skillTags, setSkillTags] = useState((initial?.skill_tags ?? []).join(", "));
  const [highlights, setHighlights] = useState((initial?.highlights ?? []).join("\n"));
  const [metrics, setMetrics] = useState<DraftMetric[]>(
    (initial?.metrics ?? []).map((m) => ({
      name: m.name,
      value: m.value,
      context: m.context ?? "",
    })),
  );
  const [variants, setVariants] = useState<DraftVariant[]>(
    (initial?.variants ?? []).map((v) => ({
      direction: v.direction,
      text: v.text,
      note: v.note ?? "",
    })),
  );

  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const updateMetric = (i: number, patch: Partial<DraftMetric>) =>
    setMetrics((prev) => prev.map((m, idx) => (idx === i ? { ...m, ...patch } : m)));
  const updateVariant = (i: number, patch: Partial<DraftVariant>) =>
    setVariants((prev) => prev.map((v, idx) => (idx === i ? { ...v, ...patch } : v)));

  const validate = (): string | null => {
    if (!org.trim()) return "请填写「组织 / 公司 / 项目名」（org）。";
    if (!role.trim()) return "请填写「角色 / 职位」（role）。";
    if (!rawDescription.trim()) return "请填写「原始描述」（raw_description）。";
    const dirs = variants.map((v) => v.direction.trim()).filter(Boolean);
    const dup = dirs.find((d, i) => dirs.indexOf(d) !== i);
    if (dup) {
      return `同一个岗位方向「${dup}」出现了两条，后端会拒绝（422）。请合并或删除重复项。`;
    }
    return null;
  };

  const submit = async () => {
    const err = validate();
    if (err) {
      setFormError(err);
      return;
    }
    setFormError(null);
    setSubmitting(true);

    const payload: ExperienceCreate = {
      kind,
      org: org.trim(),
      role: role.trim(),
      raw_description: rawDescription.trim(),
      start_date: startDate || null,
      end_date: endDate || null,
      // 英文逗号分隔 → 数组
      skill_tags: skillTags
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      // 一行一条 → 数组
      highlights: highlights
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      metrics: metrics
        .filter((m) => m.name.trim() && m.value.trim())
        .map((m) => ({
          name: m.name.trim(),
          value: m.value.trim(),
          context: m.context.trim() || null,
        })),
      variants: variants
        .filter((v) => v.direction.trim() && v.text.trim())
        .map((v) => ({
          direction: v.direction.trim(),
          text: v.text.trim(),
          note: v.note.trim() || null,
        })),
      // sort_order 在后端有默认值，但类型管线把「带 default 的字面量字段」
      // 生成为**必填**（M1 记录在案的契约现象），不传会 TS 编译报错。
      // 显式传 0，不要在此处用 ?? 0 兜底去掩盖契约定义。
      sort_order: 0,
    };

    try {
      if (isEdit && initial) {
        // PUT 全量替换：必须把上面**所有**字段都发出去，不能只发改动的那几个，
        // 否则后端会把没传的字段当成空值（例如不传 skill_tags 等于清空标签）。
        await api.updateExperience(initial.id, payload, { requestId: newRequestId() });
      } else {
        await api.createExperience(payload, { requestId: newRequestId() });
      }
      onSaved();
    } catch (error) {
      setFormError(
        error instanceof ApiError
          ? `保存失败（${error.status}）：${error.body.slice(0, 500)}`
          : String(error),
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="mb-8 rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="font-mono text-sm">{isEdit ? "编辑经历" : "新增经历"}</h2>
        <button type="button" onClick={onCancel} className={secondaryBtn}>
          取消
        </button>
      </div>

      <div className="grid gap-4">
        <div>
          <label className={labelCls}>类型</label>
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as ExperienceCreate["kind"])}
            className={inputCls}
          >
            <option value="project">项目</option>
            <option value="internship">实习</option>
            <option value="campus">校园</option>
          </select>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className={labelCls}>组织 / 公司 / 项目名 *</label>
            <input
              className={inputCls}
              value={org}
              onChange={(e) => setOrg(e.target.value)}
              placeholder="如：某互联网公司 / 简历优化器"
            />
          </div>
          <div>
            <label className={labelCls}>角色 / 职位 *</label>
            <input
              className={inputCls}
              value={role}
              onChange={(e) => setRole(e.target.value)}
              placeholder="如：后端开发 / 独立开发"
            />
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className={labelCls}>开始时间</label>
            <input
              type="date"
              className={inputCls}
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </div>
          <div>
            <label className={labelCls}>结束时间（留空 = 进行中）</label>
            <input
              type="date"
              className={inputCls}
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </div>
        </div>

        <div>
          <label className={labelCls}>原始描述 *（事实基线）</label>
          <textarea
            className={inputCls}
            rows={4}
            value={rawDescription}
            onChange={(e) => setRawDescription(e.target.value)}
            placeholder="这段经历的原始描述，后续改写都要可回溯到这里。"
          />
        </div>

        <div>
          <label className={labelCls}>技能标签（英文逗号分隔）</label>
          <input
            className={inputCls}
            value={skillTags}
            onChange={(e) => setSkillTags(e.target.value)}
            placeholder="Python, FastAPI, LangGraph"
          />
        </div>

        <div>
          <label className={labelCls}>定性要点（一行一条）</label>
          <textarea
            className={inputCls}
            rows={4}
            value={highlights}
            onChange={(e) => setHighlights(e.target.value)}
            placeholder={"接入 Langfuse 观测，每次 LLM 调用可按 trace_id 回放\n用 LangGraph 编排多步流程"}
          />
        </div>

        {/* 量化结果：与定性要点分开 */}
        <div>
          <label className={labelCls}>量化结果（指标名 / 数值 / 口径）</label>
          <div className="space-y-2">
            {metrics.map((m, i) => (
              <div key={i} className="flex gap-2">
                <input
                  className={inputCls}
                  value={m.name}
                  onChange={(e) => updateMetric(i, { name: e.target.value })}
                  placeholder="指标名"
                />
                <input
                  className={inputCls}
                  value={m.value}
                  onChange={(e) => updateMetric(i, { value: e.target.value })}
                  placeholder="数值"
                />
                <input
                  className={inputCls}
                  value={m.context}
                  onChange={(e) => updateMetric(i, { context: e.target.value })}
                  placeholder="口径（可选）"
                />
                <button
                  type="button"
                  onClick={() => setMetrics((prev) => prev.filter((_, idx) => idx !== i))}
                  className={secondaryBtn}
                  aria-label="删除该行"
                >
                  ×
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() =>
                setMetrics((prev) => [...prev, { name: "", value: "", context: "" }])
              }
              className={secondaryBtn}
            >
              + 添加指标
            </button>
          </div>
        </div>

        {/* 岗位方向变体 */}
        <div>
          <label className={labelCls}>岗位方向变体（可选）</label>
          <div className="space-y-3">
            {variants.map((v, i) => (
              <div
                key={i}
                className="space-y-2 rounded-md border border-[var(--border)] bg-[var(--panel-2)] p-3"
              >
                <div className="flex gap-2">
                  <input
                    className={inputCls}
                    value={v.direction}
                    onChange={(e) => updateVariant(i, { direction: e.target.value })}
                    placeholder="岗位方向，如：后端开发"
                  />
                  <button
                    type="button"
                    onClick={() => setVariants((prev) => prev.filter((_, idx) => idx !== i))}
                    className={secondaryBtn}
                    aria-label="删除该行"
                  >
                    ×
                  </button>
                </div>
                <textarea
                  className={inputCls}
                  rows={3}
                  value={v.text}
                  onChange={(e) => updateVariant(i, { text: e.target.value })}
                  placeholder="该方向下的表述"
                />
                <input
                  className={inputCls}
                  value={v.note}
                  onChange={(e) => updateVariant(i, { note: e.target.value })}
                  placeholder="备注（可选）"
                />
              </div>
            ))}
            <button
              type="button"
              onClick={() =>
                setVariants((prev) => [...prev, { direction: "", text: "", note: "" }])
              }
              className={secondaryBtn}
            >
              + 添加变体
            </button>
          </div>
        </div>

        {formError ? (
          <div className="rounded-md border border-[var(--err)]/40 bg-[var(--err)]/10 px-3 py-3">
            <p className="font-mono text-xs break-all text-[var(--err)]">{formError}</p>
          </div>
        ) : null}

        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => void submit()}
            disabled={submitting}
            className="rounded-md px-4 py-2.5 text-sm font-medium transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
            style={{ background: "var(--accent)", color: "#0b0d12" }}
          >
            {submitting ? "保存中…" : isEdit ? "保存修改" : "创建经历"}
          </button>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ *
 * 页面
 * ------------------------------------------------------------------ */

export default function LibraryPage() {
  const [items, setItems] = useState<ExperienceRead[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [editing, setEditing] = useState<
    { mode: "create" } | { mode: "edit"; item: ExperienceRead } | null
  >(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listExperiences({ requestId: newRequestId() });
      setItems(data.items);
      setTotal(data.total);
      setListError(null);
    } catch (error) {
      setListError(
        error instanceof ApiError
          ? `读取失败（${error.status}）：${error.body.slice(0, 300)}`
          : String(error),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const onDelete = async (item: ExperienceRead) => {
    if (!window.confirm(`确认删除「${item.org} · ${item.role}」？此操作不可撤销。`)) {
      return;
    }
    try {
      await api.deleteExperience(item.id, { requestId: newRequestId() });
      await reload();
    } catch (error) {
      setListError(
        error instanceof ApiError
          ? `删除失败（${error.status}）：${error.body.slice(0, 300)}`
          : String(error),
      );
    }
  };

  const editingKey =
    editing?.mode === "edit" ? editing.item.id : editing?.mode === "create" ? "create" : null;

  return (
    <main className="mx-auto w-full max-w-6xl px-6 py-8">
      <TopNav />

      <header className="mb-6 flex items-end justify-between gap-4">
        <div>
          <p className="font-mono text-xs tracking-widest text-[var(--muted)] uppercase">
            M2 · 素材库
          </p>
          <h1 className="mt-2 text-2xl font-semibold">经历素材库</h1>
          <p className="mt-2 text-sm text-[var(--muted)]">
            录入你的经历，按岗位方向维护多版本表述，生成简历时直接勾选。
          </p>
        </div>
        <button
          type="button"
          onClick={() => setEditing({ mode: "create" })}
          disabled={editing !== null}
          className="shrink-0 rounded-md px-4 py-2.5 text-sm font-medium transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
          style={{ background: "var(--accent)", color: "#0b0d12" }}
        >
          新增经历
        </button>
      </header>

      {editing ? (
        <ExperienceForm
          key={editingKey ?? "create"}
          initial={editing.mode === "edit" ? editing.item : null}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void reload();
          }}
        />
      ) : null}

      {listError ? (
        <div className="mb-6 rounded-md border border-[var(--err)]/40 bg-[var(--err)]/10 px-3 py-3">
          <p className="font-mono text-xs break-all text-[var(--err)]">{listError}</p>
        </div>
      ) : null}

      {loading ? (
        <p className="text-sm text-[var(--muted)]">读取中…</p>
      ) : (
        <>
          <p className="mb-4 font-mono text-xs text-[var(--muted)]">共 {total} 条经历</p>

          {items.length === 0 ? (
            <div className="rounded-xl border border-[var(--border)] bg-[var(--panel)] p-8 text-center">
              <p className="text-sm text-[var(--muted)]">
                还没有任何经历。点击右上角「新增经历」，录入你的第一段经历。
              </p>
            </div>
          ) : (
            GROUP_ORDER.map(({ kind, title }) => {
              const rows = items.filter((it) => it.kind === kind);
              if (rows.length === 0) return null;
              return (
                <section key={kind} className="mb-8">
                  <h2 className="mb-3 font-mono text-sm text-[var(--muted)] uppercase">
                    {title}
                  </h2>
                  <div className="space-y-4">
                    {rows.map((item) => (
                      <ExperienceCard
                        key={item.id}
                        item={item}
                        onEdit={(it) => setEditing({ mode: "edit", item: it })}
                        onDelete={(it) => void onDelete(it)}
                      />
                    ))}
                  </div>
                </section>
              );
            })
          )}
        </>
      )}
    </main>
  );
}
