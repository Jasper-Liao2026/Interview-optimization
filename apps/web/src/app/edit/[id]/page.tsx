"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useRef, useState } from "react";

import { TopNav } from "@/components/top-nav";
import { PdfPreview } from "@/components/pdf-preview";
import { api, ApiError, newRequestId, type AiEditResponse, type EditorResponse, type ResumeDraft } from "@/lib/api-client";

const fieldClass = "w-full rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm disabled:opacity-50";
const buttonClass = "rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2 text-xs disabled:opacity-40";
const panelClass = "rounded-lg border border-[var(--border)] bg-[var(--panel)] p-4";
const pendingKey = (id: string) => `resume-optimizer:pending-edit:${id}`;

function draftOf(editor: EditorResponse): ResumeDraft {
  return { title: editor.resume.title, header: editor.resume.header, sections: editor.resume.sections };
}

function rememberPending(id: string, runId: string | null) {
  try {
    if (runId) localStorage.setItem(pendingKey(id), runId);
    else localStorage.removeItem(pendingKey(id));
  } catch { /* The editor also works without browser storage. */ }
}

function failure(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) return "简历已在其他操作中更新，或提案所依据的版本已过期。请刷新最新版本后重试。";
  return error instanceof ApiError ? `操作失败（${error.status}）：${error.body.slice(0, 300)}` : `操作失败：${String(error)}`;
}

function bulletText(value: AiEditResponse["proposed_bullets"][number]): string {
  return typeof value.text === "string" ? value.text : "（无文本）";
}

export default function EditPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [editor, setEditor] = useState<EditorResponse | null>(null);
  const [draft, setDraft] = useState<ResumeDraft | null>(null);
  const [proposal, setProposal] = useState<AiEditResponse | null>(null);
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const [selection, setSelection] = useState("");
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ url: string; snapshot: string } | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const operation = useRef<AbortController | null>(null);
  const previewUrl = useRef<string | null>(null);
  const snapshot = draft ? JSON.stringify(draft) : "";
  const dirty = !!editor && snapshot !== JSON.stringify(draftOf(editor));
  const pending = proposal?.status === "pending";
  const locked = busy || pending || recoveryBlocked;

  const installEditor = useCallback((value: EditorResponse) => {
    setEditor(value);
    setDraft(draftOf(value));
    setSelection("");
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    operation.current?.abort();
    operation.current = controller;
    setBusy(true);
    setError(null);
    setEditor(null);
    setDraft(null);
    setProposal(null);
    setRecoveryBlocked(false);
    const opts = { signal: controller.signal, requestId: newRequestId() };
    (async () => {
      try {
        const value = await api.getEditor(id, opts);
        if (controller.signal.aborted) return;
        installEditor(value);
        try { localStorage.setItem("resume-optimizer:last-edited-resume", id); } catch { /* Optional shortcut. */ }
        let runId: string | null = null;
        try { runId = localStorage.getItem(pendingKey(id)); } catch { /* Optional recovery. */ }
        if (runId) {
          setRecoveryBlocked(true);
          try {
            const saved = await api.getAiEdit(id, runId, opts);
            if (controller.signal.aborted) return;
            if (saved.status === "pending") setProposal(saved);
            else rememberPending(id, null);
            setRecoveryBlocked(false);
          } catch (cause: unknown) {
            if (controller.signal.aborted) return;
            if (cause instanceof ApiError && cause.status === 404) { rememberPending(id, null); setRecoveryBlocked(false); }
            else throw cause;
          }
        }
      } catch (cause: unknown) {
        if (!controller.signal.aborted) setError(failure(cause));
      } finally {
        if (!controller.signal.aborted) { setBusy(false); operation.current = null; }
      }
    })();
    return () => { controller.abort(); operation.current?.abort(); };
  }, [id, refreshToken, installEditor]);

  useEffect(() => {
    if (!snapshot) return;
    const controller = new AbortController();
    setPreviewError(null);
    const timer = window.setTimeout(async () => {
      try {
        const blob = await api.previewDraft(id, JSON.parse(snapshot) as ResumeDraft, { signal: controller.signal, requestId: newRequestId() });
        if (controller.signal.aborted) return;
        const url = URL.createObjectURL(blob);
        const oldUrl = previewUrl.current;
        previewUrl.current = url;
        setPreview({ url, snapshot });
        if (oldUrl) URL.revokeObjectURL(oldUrl);
      } catch (cause: unknown) {
        if (!controller.signal.aborted) setPreviewError(failure(cause));
      }
    }, 600);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [id, snapshot]);

  useEffect(() => () => {
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
  }, []);


  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function mutate(action: (signal: AbortSignal) => Promise<void>) {
    if (operation.current) return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy(true);
    setError(null);
    setNotice(null);
    try { await action(controller.signal); }
    catch (cause: unknown) { if (!controller.signal.aborted) setError(failure(cause)); }
    finally { if (!controller.signal.aborted) { operation.current = null; setBusy(false); } }
  }

  function change(update: (next: ResumeDraft) => void) {
    if (locked || operation.current || !draft) return;
    const next = structuredClone(draft);
    update(next);
    setDraft(next);
    setNotice("手工修改的事实请自行核对；改过的要点已清空原有事实来源。");
  }

  function save() {
    if (!draft || !editor || pending) return;
    if (!draft.title.trim() || draft.sections.some(section => section.entries.some(entry => entry.bullets.some(bullet => !bullet.text.trim())))) {
      setError("请填写简历标题，并填写或删除空白要点。"); return;
    }
    void mutate(async signal => {
      const value = await api.saveEditor(id, { ...draft, expected_revision: editor.revision }, { signal, requestId: newRequestId() });
      if (signal.aborted) return;
      installEditor(value);
      setNotice("已保存为新版本。");
    });
  }

  function restore(revisionId: string) {
    if (!editor || dirty || pending) return;
    void mutate(async signal => {
      const value = await api.restoreRevision(id, revisionId, { expected_revision: editor.revision }, { signal, requestId: newRequestId() });
      if (signal.aborted) return;
      installEditor(value);
      setNotice("已恢复历史内容，并保留为一个新版本。");
    });
  }

  function requestAi() {
    if (!editor || dirty || pending || !selection || !instruction.trim()) return;
    const [sectionIndex, entryIndex] = selection.split(":").map(Number);
    void mutate(async signal => {
      const value = await api.createAiEdit(id, { expected_revision: editor.revision, section_index: sectionIndex!, entry_index: entryIndex!, instruction: instruction.trim() }, { signal, requestId: newRequestId() });
      if (signal.aborted) return;
      setProposal(value);
      rememberPending(id, value.status === "pending" ? value.run_id : null);
      if (value.editor) installEditor(value.editor);
    });
  }

  function decide(accept: boolean) {
    if (!proposal || dirty) return;
    void mutate(async signal => {
      const value = await api.decideAiEdit(id, proposal.run_id, { accept }, { signal, requestId: newRequestId() });
      if (signal.aborted) return;
      setProposal(value);
      rememberPending(id, value.status === "pending" ? value.run_id : null);
      if (value.editor) installEditor(value.editor);
      setNotice(accept ? "已确认并保存 AI 改写。" : "已拒绝提案。");
    });
  }

  const previous = editor?.history.filter(item => item.revision < editor.revision).sort((a, b) => b.revision - a.revision)[0];
  const previewReady = !!preview && preview.snapshot === snapshot;

  return <main className="mx-auto w-full max-w-[1600px] px-6 py-8">
    <TopNav />
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div><h1 className="text-xl font-semibold">编辑简历</h1><p className="mt-1 text-xs text-[var(--muted)]">{editor ? `版本 ${editor.revision} · ${dirty ? "有未保存修改" : "已保存"}` : "正在读取简历…"} · 预览与 PDF 下载使用同一份文件</p></div>
      <div className="flex flex-wrap gap-2">
        <button className={buttonClass} disabled={locked || !dirty} onClick={save}>{busy ? "处理中…" : "保存新版本"}</button>
        <button className={buttonClass} disabled={locked || dirty || !previous} onClick={() => previous && restore(previous.id)}>撤销到上一版</button>
        <button className={buttonClass} disabled={busy} onClick={() => { if (!dirty || window.confirm("刷新将丢弃未保存修改，是否继续？")) setRefreshToken(value => value + 1); }}>刷新最新版本</button>
        {previewReady ? <a className={buttonClass} href={preview.url} download={`${draft?.title || "简历"}.pdf`}>下载当前 PDF</a> : <button className={buttonClass} disabled>PDF 准备中</button>}
      </div>
    </div>
    {error ? <p role="alert" className="mb-4 rounded-md border border-[var(--err)]/40 bg-[var(--err)]/10 p-3 text-sm text-[var(--err)]">{error}</p> : null}
    {notice ? <p role="status" className="mb-4 text-xs text-[var(--warn)]">{notice}</p> : null}
    {recoveryBlocked ? <p className="mb-4 text-xs text-[var(--warn)]">上次 AI 提案尚未读取完成。请刷新最新版本恢复提案后继续编辑。</p> : null}
    {!draft ? <Link className="text-sm text-[var(--accent)]" href="/generate">返回生成简历</Link> : <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        <section className={panelClass}>
          <h2 className="mb-3 text-sm font-semibold">基本信息</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-[var(--muted)]">简历标题<input className={`${fieldClass} mt-1`} maxLength={120} value={draft.title} disabled={locked} onChange={event => change(next => { next.title = event.target.value; })} /></label>
            <label className="text-xs text-[var(--muted)]">姓名<input className={`${fieldClass} mt-1`} value={draft.header.name} disabled={locked} onChange={event => change(next => { next.header.name = event.target.value; })} /></label>
            <label className="text-xs text-[var(--muted)] sm:col-span-2">个人简介<input className={`${fieldClass} mt-1`} value={draft.header.headline ?? ""} disabled={locked} onChange={event => change(next => { next.header.headline = event.target.value || null; })} /></label>
          </div>
        </section>
        {draft.sections.map((section, si) => <section className={panelClass} key={si}>
          <div className="mb-3 flex gap-2"><input aria-label={`第 ${si + 1} 个分区标题`} className={fieldClass} value={section.title} disabled={locked} onChange={event => change(next => { next.sections[si]!.title = event.target.value; })} /><button className={`${buttonClass} shrink-0`} disabled={locked} onClick={() => change(next => { next.sections.splice(si, 1); })}>删除分区</button></div>
          <div className="space-y-4">{section.entries.map((entry, ei) => <div key={ei} className="rounded-md border border-[var(--border)] p-3">
            <div className="mb-3 flex items-center justify-between gap-2"><span className="text-xs text-[var(--muted)]">经历 {ei + 1}{entry.experience_id ? " · 来自素材库" : " · 手工新增"}</span><button className={buttonClass} disabled={locked} onClick={() => change(next => { next.sections[si]!.entries.splice(ei, 1); })}>删除经历</button></div>
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="text-xs text-[var(--muted)]">组织 / 项目<input className={`${fieldClass} mt-1`} value={entry.org} disabled={locked} onChange={event => change(next => { next.sections[si]!.entries[ei]!.org = event.target.value; })} /></label>
              <label className="text-xs text-[var(--muted)]">角色<input className={`${fieldClass} mt-1`} value={entry.role} disabled={locked} onChange={event => change(next => { next.sections[si]!.entries[ei]!.role = event.target.value; })} /></label>
              <label className="text-xs text-[var(--muted)] sm:col-span-2">时间<input className={`${fieldClass} mt-1`} value={entry.period ?? ""} disabled={locked} onChange={event => change(next => { next.sections[si]!.entries[ei]!.period = event.target.value || null; })} /></label>
            </div>
            <div className="my-3 space-y-2">{entry.bullets.map((bullet, bi) => <div key={bi}>
              <div className="flex items-start gap-2"><textarea aria-label={`经历 ${ei + 1} 要点 ${bi + 1}`} className={fieldClass} rows={3} value={bullet.text} disabled={locked} onChange={event => change(next => { next.sections[si]!.entries[ei]!.bullets[bi] = { text: event.target.value, evidence: [] }; })} /><button className={`${buttonClass} shrink-0`} disabled={locked} onClick={() => change(next => { next.sections[si]!.entries[ei]!.bullets.splice(bi, 1); })}>删除</button></div>
              <p className="mt-1 text-[11px] text-[var(--muted)]" title={bullet.evidence.join("\n")}>{bullet.evidence.length ? `${bullet.evidence.length} 条事实来源` : "无事实来源 · 请自行核对"}</p>
            </div>)}</div>
            <button className={buttonClass} disabled={locked} onClick={() => change(next => { next.sections[si]!.entries[ei]!.bullets.push({ text: "", evidence: [] }); })}>＋ 添加要点</button>
          </div>)}</div>
          <button className={`${buttonClass} mt-3`} disabled={locked} onClick={() => change(next => { next.sections[si]!.entries.push({ experience_id: null, kind: "project", org: "", role: "", period: null, bullets: [{ text: "", evidence: [] }] }); })}>＋ 添加经历</button>
        </section>)}
        <button className={buttonClass} disabled={locked || draft.sections.length >= 20} onClick={() => change(next => { next.sections.push({ title: "新分区", entries: [] }); })}>＋ 添加分区</button>
        <section className={panelClass}>
          <h2 className="mb-2 text-sm font-semibold">AI 局部改写</h2>
          <p className="mb-3 text-xs text-[var(--muted)]">选择一段经历并描述修改目标，核对提案后确认保存。AI 仅修改要点文本。</p>
          {dirty ? <p className="mb-3 text-xs text-[var(--warn)]">请先保存当前修改，再使用 AI 改写或恢复历史。</p> : null}
          <label className="text-xs text-[var(--muted)]">目标经历<select className={`${fieldClass} mt-1`} value={selection} disabled={locked || dirty} onChange={event => setSelection(event.target.value)}><option value="">请选择经历</option>{draft.sections.flatMap((section, si) => section.entries.map((entry, ei) => <option key={`${si}:${ei}`} value={`${si}:${ei}`}>{section.title} · {entry.org || "未命名"} · {entry.role}</option>))}</select></label>
          <label className="mt-3 block text-xs text-[var(--muted)]">改写指令<textarea className={`${fieldClass} mt-1`} rows={3} maxLength={2000} placeholder="例如：强调工程落地与协作，压缩为三条，保留原有事实。" value={instruction} disabled={locked || dirty} onChange={event => setInstruction(event.target.value)} /></label>
          <button className={`${buttonClass} mt-3`} disabled={locked || dirty || !selection || !instruction.trim()} onClick={requestAi}>生成改写提案</button>
          {proposal ? <div className="mt-4 border-t border-[var(--border)] pt-4">
            <p className="mb-3 text-xs font-medium">{pending ? "待确认提案 · 确认或拒绝后继续编辑" : proposal.status === "applied" ? "提案已应用" : "提案已拒绝"}{proposal.is_stub ? " · 当前为演示数据" : ""}</p>
            <p className="mb-3 text-xs text-[var(--muted)]">指令：{proposal.instruction}</p>
            <div className="grid gap-3 sm:grid-cols-2">{[{ label: "修改前", bullets: proposal.original_bullets }, { label: "改写提案", bullets: proposal.proposed_bullets }].map(({ label, bullets }) => <div key={label} className="rounded-md bg-[var(--panel-2)] p-3"><p className="mb-2 text-xs text-[var(--muted)]">{label}</p><ul className="list-disc space-y-2 pl-4 text-xs">{bullets.map((bullet, index) => <li key={index}>{bulletText(bullet)}</li>)}</ul></div>)}</div>
            {proposal.warnings.map((warning, index) => <p key={index} className="mt-2 text-xs text-[var(--warn)]">{warning}</p>)}
            {pending ? <div className="mt-3 flex gap-2"><button className={buttonClass} disabled={busy || dirty || proposal.base_revision !== editor?.revision} onClick={() => decide(true)}>确认改写并保存</button><button className={buttonClass} disabled={busy} onClick={() => decide(false)}>拒绝提案</button></div> : null}
            {pending && proposal.base_revision !== editor?.revision ? <p className="mt-2 text-xs text-[var(--warn)]">此提案基于旧版本，请拒绝后重新生成。</p> : null}
          </div> : null}
        </section>
        <section className={panelClass}>
          <h2 className="mb-3 text-sm font-semibold">版本历史</h2>
          <div className="space-y-2">{editor?.history.slice().sort((a, b) => b.revision - a.revision).map(item => <div className="flex items-center justify-between gap-2 text-xs" key={item.id}><div><p>版本 {item.revision} · {item.reason}{item.revision === editor.revision ? " · 当前" : ""}</p><p className="mt-1 text-[var(--muted)]">{new Date(item.created_at).toLocaleString("zh-CN")}</p></div><button className={buttonClass} disabled={locked || dirty || item.revision === editor.revision} onClick={() => restore(item.id)}>恢复</button></div>)}</div>
        </section>
      </div>
      <section className={`${panelClass} xl:sticky xl:top-6`}>
        <div className="mb-3 flex items-center justify-between"><h2 className="text-sm font-semibold">PDF 分页预览</h2><span className="text-xs text-[var(--muted)]">{previewReady ? "已更新" : "正在更新…"}</span></div>
        {previewError ? <p role="alert" className="mb-3 text-xs text-[var(--err)]">{previewError}</p> : null}
        {preview ? <PdfPreview url={preview.url} /> : <div className="flex h-[70vh] items-center justify-center text-sm text-[var(--muted)]">正在准备 PDF…</div>}
        <p className="mt-2 text-[11px] text-[var(--muted)]">编辑后自动更新预览。下载按钮导出当前预览的同一份 PDF，未保存内容也会包含在内。</p>
      </section>
    </div>}
  </main>;
}
