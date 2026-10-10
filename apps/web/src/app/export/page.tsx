"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { PdfPreview } from "@/components/pdf-preview";
import { TopNav } from "@/components/top-nav";
import {
  api, ApiError, newRequestId,
  type BatchGenerateRequest, type BatchGenerateResponse, type ExperienceRead,
  type JdRead, type ResumeRead, type TemplateListResponse,
} from "@/lib/api-client";

const BATCH_STORAGE_KEY = "resume-optimizer:last-export-batch";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const button = "rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-40";
const panel = "rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5";
const input = "rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-xs";
const statuses = { completed: "成功", partial: "部分完成", failed: "失败" };
const MAX_JOBS = 20;
const MAX_EXPERIENCES = 60;
const MAX_ZIP_RESUMES = 20;

function newBatchId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 15) | 64;
  bytes[8] = (bytes[8]! & 63) | 128;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function rememberBatch(value: BatchGenerateRequest | null) {
  try {
    if (value) localStorage.setItem(BATCH_STORAGE_KEY, JSON.stringify(value));
    else localStorage.removeItem(BATCH_STORAGE_KEY);
  } catch { /* In-memory retries also work when storage is unavailable. */ }
}

function readBatch(): BatchGenerateRequest | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(BATCH_STORAGE_KEY) ?? "null");
    if (!value || typeof value !== "object") return null;
    const item = value as Record<string, unknown>;
    const ids = (list: unknown): list is string[] => Array.isArray(list) && list.length > 0 && list.every(id => typeof id === "string" && UUID.test(id));
    if (typeof item.batch_id !== "string" || !UUID.test(item.batch_id) || !ids(item.jd_ids) || !ids(item.experience_ids)) return null;
    return { batch_id: item.batch_id, jd_ids: item.jd_ids, experience_ids: item.experience_ids };
  } catch { return null; }
}

function failure(error: unknown, action: string) {
  return error instanceof ApiError ? `${action}（${error.status}）：${error.body.slice(0, 500)}` : `${action}：${String(error)}`;
}

function toggleSelection(selected: Set<string>, id: string) {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

function hasBullets(resume: ResumeRead) {
  return resume.sections.some(section => section.entries.some(entry => entry.bullets.length > 0));
}

function toggleBounded(selected: Set<string>, id: string, limit: number) {
  if (!selected.has(id) && selected.size >= limit) return selected;
  return toggleSelection(selected, id);
}

function isStub(resume: ResumeRead) {
  return !!resume.generator?.startsWith("stub:") || JSON.stringify(resume.sections).includes("【fixture】");
}

export default function ExportPage() {
  const [jobs, setJobs] = useState<JdRead[]>([]);
  const [experiences, setExperiences] = useState<ExperienceRead[]>([]);
  const [resumes, setResumes] = useState<ResumeRead[]>([]);
  const [templates, setTemplates] = useState<TemplateListResponse["items"]>([]);
  const [template, setTemplate] = useState("");
  const [selectedJobs, setSelectedJobs] = useState<Set<string>>(new Set());
  const [selectedExperiences, setSelectedExperiences] = useState<Set<string>>(new Set());
  const [selectedResumes, setSelectedResumes] = useState<Set<string>>(new Set());
  const [batch, setBatch] = useState<BatchGenerateRequest | null>(null);
  const [result, setResult] = useState<BatchGenerateResponse | null>(null);
  const [keyword, setKeyword] = useState("");
  const [jobFilter, setJobFilter] = useState("");
  const [bulletFilter, setBulletFilter] = useState("all");
  const [busy, setBusy] = useState<"loading" | "generate" | "preview" | "export" | "refresh" | null>("loading");
  const [refreshNeeded, setRefreshNeeded] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ id: string; template: string; url: string } | null>(null);
  const active = useRef<AbortController | null>(null);
  const previewUrl = useRef<string | null>(null);
  const downloads = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const clearPreview = useCallback(() => {
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = null;
    setPreview(null);
  }, []);

  const load = useCallback(async () => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy("loading");
    setError(null);
    try {
      const opts = { signal: controller.signal, requestId: newRequestId() };
      const [jobData, experienceData, resumeData, templateData] = await Promise.all([
        api.listJobs(opts), api.listExperiences(opts), api.listResumes(opts), api.listTemplates(opts),
      ]);
      if (controller.signal.aborted) return;
      setJobs(jobData.items);
      setExperiences(experienceData.items);
      setResumes(resumeData.items);
      setTemplates(templateData.items);
      setTemplate(templateData.items[0]?.id ?? "");
      const saved = readBatch();
      if (saved && saved.experience_ids && saved.jd_ids.every(id => jobData.items.some(job => job.id === id)) && saved.experience_ids.every(id => experienceData.items.some(experience => experience.id === id))) {
        setBatch(saved);
        setSelectedJobs(new Set(saved.jd_ids));
        setSelectedExperiences(new Set(saved.experience_ids));
        setNotice("已恢复上次批次。点击“重试此批”可读取生成记录，避免重复创建；失败经历可单独恢复。");
      }
      const requested = new URLSearchParams(window.location.search).get("resume");
      if (requested && resumeData.items.some(resume => resume.id === requested && hasBullets(resume))) setSelectedResumes(new Set([requested]));
      setLoaded(true);
    } catch (cause) {
      if (!controller.signal.aborted) setError(failure(cause, "导出数据读取失败"));
    } finally {
      if (active.current === controller) {
        active.current = null;
        setBusy(null);
      }
    }
  }, []);

  useEffect(() => {
    const pendingDownloads = downloads.current;
    void load();
    return () => {
      active.current?.abort();
      active.current = null;
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
      for (const [url, timer] of pendingDownloads) {
        clearTimeout(timer);
        URL.revokeObjectURL(url);
      }
      pendingDownloads.clear();
    };
  }, [load]);

  function cancel() {
    active.current?.abort();
    active.current = null;
    setBusy(null);
    setNotice("已取消等待。若生成已提交，可重试同一批读取结果。");
  }

  function resetBatch() {
    setBatch(null);
    setResult(null);
    rememberBatch(null);
    setNotice(null);
  }

  async function generate() {
    if (active.current || !loaded || !selectedJobs.size || !selectedExperiences.size || selectedJobs.size > MAX_JOBS || selectedExperiences.size > MAX_EXPERIENCES) return;
    const request: BatchGenerateRequest = batch ?? { batch_id: newBatchId(), jd_ids: [...selectedJobs], experience_ids: [...selectedExperiences] };
    setBatch(request);
    rememberBatch(request);
    const controller = new AbortController();
    active.current = controller;
    setBusy("generate");
    setError(null);
    setNotice(null);
    clearPreview();
    try {
      const response = await api.batchGenerate(request, { signal: controller.signal, requestId: newRequestId() });
      if (controller.signal.aborted || active.current !== controller) return;
      setResult(response);
      setRefreshNeeded(true);
      try {
        // Batch results are immutable run snapshots; export uses current edits.
        const current = await api.listResumes({ signal: controller.signal, requestId: newRequestId() });
        if (controller.signal.aborted || active.current !== controller) return;
        setResumes(current.items);
        setSelectedResumes(previous => new Set([...previous].filter(id => current.items.some(resume => resume.id === id && hasBullets(resume))).slice(0, MAX_ZIP_RESUMES)));
        setRefreshNeeded(false);
        setNotice("本批结果已保留，简历列表已刷新为当前编辑内容。相同批次重试会复用生成记录；要创建另一批，请点击“开始新一批”。");
      } catch (refreshCause) {
        if (!controller.signal.aborted) setNotice(`批次结果已保留，但最新简历列表刷新失败：${failure(refreshCause, "列表刷新失败")}。请点击“重试刷新简历列表”。`);
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(failure(cause, "批量生成失败，请重试此批"));
    } finally {
      if (active.current === controller) { active.current = null; setBusy(null); }
    }
  }

  async function showPreview(resume: ResumeRead) {
    if (active.current || !template) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy("preview");
    setError(null);
    clearPreview();
    try {
      const blob = await api.fetchResumePdf(resume.id, template, { signal: controller.signal, requestId: newRequestId() });
      if (controller.signal.aborted || active.current !== controller) return;
      const url = URL.createObjectURL(blob);
      previewUrl.current = url;
      setPreview({ id: resume.id, template, url });
    } catch (cause) {
      if (!controller.signal.aborted) setError(failure(cause, "PDF 预览失败"));
    } finally {
      if (active.current === controller) { active.current = null; setBusy(null); }
    }
  }

  async function refreshResumes() {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy("refresh");
    setError(null);
    clearPreview();
    try {
      const current = await api.listResumes({ signal: controller.signal, requestId: newRequestId() });
      if (controller.signal.aborted || active.current !== controller) return;
      setResumes(current.items);
      setSelectedResumes(previous => new Set([...previous].filter(id => current.items.some(resume => resume.id === id && hasBullets(resume))).slice(0, MAX_ZIP_RESUMES)));
      setRefreshNeeded(false);
      setNotice("简历列表已刷新为当前编辑内容，批次结果仍已保留。");
    } catch (cause) {
      if (!controller.signal.aborted) setError(failure(cause, "简历列表刷新失败，可重试刷新"));
    } finally {
      if (active.current === controller) { active.current = null; setBusy(null); }
    }
  }

  async function exportZip() {
    if (active.current || !template || !selectedResumes.size || selectedResumes.size > MAX_ZIP_RESUMES || [...selectedResumes].some(id => !resumes.some(resume => resume.id === id && hasBullets(resume)))) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy("export");
    setError(null);
    try {
      const blob = await api.batchExport({ resume_ids: [...selectedResumes], template }, { signal: controller.signal, requestId: newRequestId() });
      if (controller.signal.aborted || active.current !== controller) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `resumes-${template}.zip`;
      document.body.append(link);
      link.click();
      link.remove();
      const timer = setTimeout(() => { URL.revokeObjectURL(url); downloads.current.delete(url); }, 60_000);
      downloads.current.set(url, timer);
      setNotice(`已下载 ${selectedResumes.size} 份简历的 ZIP。`);
    } catch (cause) {
      if (!controller.signal.aborted) setError(failure(cause, "ZIP 导出失败"));
    } finally {
      if (active.current === controller) { active.current = null; setBusy(null); }
    }
  }

  const query = keyword.trim().toLocaleLowerCase();
  const filtered = resumes.filter(resume => (!jobFilter || resume.jd_id === jobFilter) &&
    (bulletFilter === "all" || hasBullets(resume) === (bulletFilter === "yes")) &&
    (!query || `${resume.title} ${resume.header.name} ${JSON.stringify(resume.sections)}`.toLocaleLowerCase().includes(query)));
  const jobName = (id: string) => {
    const job = jobs.find(item => item.id === id);
    return job ? `${job.company || job.parsed?.company || "未命名公司"} · ${job.title || job.parsed?.title || "未命名岗位"}` : id;
  };

  return <main className="mx-auto w-full max-w-7xl px-6 py-8">
    <TopNav />
    <header className="mb-6">
      <h1 className="text-2xl font-semibold">批量生成与导出</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">为多个岗位分别生成简历，选择模板，预览并下载 PDF 或 ZIP。</p>
    </header>
    {error ? <p role="alert" className="mb-4 break-words rounded-md border border-[var(--err)] p-3 text-xs text-[var(--err)]">{error}</p> : null}
    {notice ? <p role="status" className="mb-4 text-xs text-[var(--muted)]">{notice}</p> : null}
    {busy ? <div className="mb-4 flex items-center gap-3"><p role="status" className="text-xs">{busy === "loading" ? "正在读取岗位、素材、简历与模板…" : busy === "generate" ? "正在逐岗位生成…" : busy === "preview" ? "正在准备 PDF…" : busy === "refresh" ? "正在刷新简历列表…" : "正在打包 ZIP…"}</p><button className={button} onClick={cancel}>取消等待</button></div> : null}
    {!loaded && !busy ? <button className={button} onClick={() => void load()}>重新读取</button> : null}
    {refreshNeeded ? <button className={`${button} mb-4`} disabled={!!busy} onClick={() => void refreshResumes()}>重试刷新简历列表</button> : null}

    <section className={`${panel} mb-5`}>
      <h2 className="mb-3 text-sm font-semibold">① 多岗位生成</h2>
      <div className="grid gap-5 md:grid-cols-2">
        <fieldset disabled={!!busy || !loaded}>
          <legend className="mb-2 text-xs text-[var(--muted)]">岗位（已选 {selectedJobs.size} / {MAX_JOBS}）</legend>
          {!jobs.length && loaded ? <p className="text-xs">还没有岗位。<Link href="/jobs" className="underline">添加岗位</Link></p> : null}
          <div className="max-h-60 space-y-2 overflow-auto">{jobs.map(job => <label key={job.id} className="flex items-start gap-2 text-xs"><input type="checkbox" disabled={!selectedJobs.has(job.id) && selectedJobs.size >= MAX_JOBS} checked={selectedJobs.has(job.id)} onChange={() => { resetBatch(); setSelectedJobs(toggleBounded(selectedJobs, job.id, MAX_JOBS)); }} /><span>{jobName(job.id)}</span></label>)}</div>
        </fieldset>
        <fieldset disabled={!!busy || !loaded}>
          <legend className="mb-2 text-xs text-[var(--muted)]">共用素材（已选 {selectedExperiences.size} / {MAX_EXPERIENCES}）</legend>
          {!experiences.length && loaded ? <p className="text-xs">还没有素材。<Link href="/library" className="underline">添加素材</Link></p> : null}
          <div className="max-h-60 space-y-2 overflow-auto">{experiences.map(experience => <label key={experience.id} className="flex items-start gap-2 text-xs"><input type="checkbox" disabled={!selectedExperiences.has(experience.id) && selectedExperiences.size >= MAX_EXPERIENCES} checked={selectedExperiences.has(experience.id)} onChange={() => { resetBatch(); setSelectedExperiences(toggleBounded(selectedExperiences, experience.id, MAX_EXPERIENCES)); }} /><span>{experience.org} · {experience.role}</span></label>)}</div>
        </fieldset>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button className={button} disabled={!!busy || !loaded || !selectedJobs.size || !selectedExperiences.size || selectedJobs.size > MAX_JOBS || selectedExperiences.size > MAX_EXPERIENCES} onClick={() => void generate()}>{batch ? "重试此批" : "生成所选岗位简历"}</button>
        {batch ? <button className={button} disabled={!!busy} onClick={resetBatch}>开始新一批</button> : null}
        {batch ? <span className="break-all font-mono text-[10px] text-[var(--muted)]">批次 {batch.batch_id}</span> : null}
      </div>
      {result ? <ul className="mt-4 space-y-3">{result.items.map(item => <li key={item.jd_id} className="rounded-md border border-[var(--border)] p-3 text-xs">
        <p className="font-medium">{jobName(item.jd_id)} · <span style={{ color: item.status === "completed" ? "var(--ok)" : item.status === "partial" ? "var(--warn)" : "var(--err)" }}>{statuses[item.status]}</span></p>
        {item.error ? <p className="mt-1 break-words text-[var(--err)]">{item.error}</p> : null}
        {item.resume && isStub(item.resume) ? <p className="mt-1 text-[var(--warn)]">当前为 stub 桩数据，包含示例内容，请勿用于正式投递。</p> : null}
        {(item.warnings ?? []).map((warning, index) => <p key={index} className="mt-1 text-[var(--warn)]">{warning}</p>)}
        {item.resume ? <Link href={`/edit/${item.resume.id}`} className="mt-2 inline-block underline">编辑该简历</Link> : null}
        {item.status !== "completed" ? <Link href={`/generate?run=${item.run_id}&jd=${item.jd_id}&experiences=${batch?.experience_ids?.join(",") ?? ""}`} className="mt-2 ml-3 inline-block underline">恢复任务 / 重试失败经历</Link> : null}
      </li>)}</ul> : null}
    </section>

    <div className="grid min-w-0 items-start gap-5 lg:grid-cols-2">
      <section className={`${panel} min-w-0`}>
        <h2 className="mb-3 text-sm font-semibold">② 选择简历与模板</h2>
        <label className="block text-xs">导出模板<select aria-label="导出模板" className={`${input} mt-2 min-w-0 w-full`} value={template} disabled={!!busy || !loaded} onChange={event => { clearPreview(); setTemplate(event.target.value); }}>{templates.map(item => <option key={item.id} value={item.id}>{item.name} · {item.layout === "two-column" ? "双栏" : "单栏"}</option>)}</select></label>
        <p className="mt-2 text-[11px] text-[var(--muted)]">{templates.find(item => item.id === template)?.description}</p>
        <div className="mt-4 flex min-w-0 flex-wrap gap-2">
          <input aria-label="筛选简历关键词" className={`${input} min-w-0 flex-1`} placeholder="关键词：标题、经历或要点" value={keyword} disabled={!!busy} onChange={event => setKeyword(event.target.value)} />
          <select aria-label="按岗位筛选简历" className={`${input} min-w-0 flex-1`} value={jobFilter} disabled={!!busy} onChange={event => setJobFilter(event.target.value)}><option value="">全部岗位</option>{jobs.map(job => <option key={job.id} value={job.id}>{jobName(job.id)}</option>)}</select>
          <select aria-label="按是否包含要点筛选" className={`${input} min-w-0 flex-1`} value={bulletFilter} disabled={!!busy} onChange={event => setBulletFilter(event.target.value)}><option value="all">全部内容</option><option value="yes">包含要点</option><option value="no">没有要点</option></select>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2"><button className={button} disabled={!!busy || !filtered.some(hasBullets) || selectedResumes.size >= MAX_ZIP_RESUMES} onClick={() => setSelectedResumes(previous => { const next = new Set(previous); for (const resume of filtered) { if (hasBullets(resume) && next.size < MAX_ZIP_RESUMES) next.add(resume.id); } return next; })}>勾选筛选结果（最多 {MAX_ZIP_RESUMES}）</button><button className={button} disabled={!!busy || !selectedResumes.size} onClick={() => setSelectedResumes(new Set())}>清空勾选</button><span className="text-[11px] text-[var(--muted)]">显示 {filtered.length} / {resumes.length} · 已选 {selectedResumes.size} / {MAX_ZIP_RESUMES}（含筛选外简历；空要点简历仅可预览）</span></div>
        <ul className="mt-3 max-h-[600px] space-y-2 overflow-auto">{filtered.map(resume => <li key={resume.id} className="rounded-md border border-[var(--border)] p-3">
          <div className="flex items-start gap-3"><input aria-label={`选择 ${resume.title}`} type="checkbox" disabled={!!busy || !hasBullets(resume) || (!selectedResumes.has(resume.id) && selectedResumes.size >= MAX_ZIP_RESUMES)} checked={selectedResumes.has(resume.id)} onChange={() => { if (hasBullets(resume)) setSelectedResumes(toggleBounded(selectedResumes, resume.id, MAX_ZIP_RESUMES)); }} /><div className="min-w-0 flex-1"><p className="text-xs font-medium">{resume.title}</p><p className="mt-1 text-[11px] text-[var(--muted)]">{resume.jd_id ? jobName(resume.jd_id) : "未绑定岗位"} · {hasBullets(resume) ? "包含要点" : "没有要点"}</p>{isStub(resume) ? <p className="mt-1 text-[11px] text-[var(--warn)]">stub 示例简历，请勿正式投递</p> : null}</div><button className={button} disabled={!!busy || !template} onClick={() => void showPreview(resume)}>预览</button></div>
        </li>)}</ul>
        {loaded && !filtered.length ? <p className="mt-3 text-xs text-[var(--muted)]">没有符合筛选条件的简历。</p> : null}
        <button className={`${button} mt-4 w-full`} disabled={!!busy || !template || !selectedResumes.size || selectedResumes.size > MAX_ZIP_RESUMES} onClick={() => void exportZip()}>下载所选 {selectedResumes.size} 份简历 ZIP（最多 {MAX_ZIP_RESUMES}）</button>
      </section>

      <section className={panel}>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-semibold">③ PDF 预览</h2>{preview && preview.template === template && !busy ? <a href={preview.url} download={`${resumes.find(resume => resume.id === preview.id)?.title || "resume"}-${template}.pdf`} className={button}>下载此 PDF</a> : null}</div>
        {preview && preview.template === template ? <><p className="mb-3 text-xs text-[var(--muted)]">{resumes.find(resume => resume.id === preview.id)?.title} · {templates.find(item => item.id === template)?.name}（下载与预览使用同一份 PDF）</p><PdfPreview url={preview.url} /></> : <p className="text-xs text-[var(--muted)]">选好模板后，点击简历的“预览”。切换模板后请重新预览。</p>}
      </section>
    </div>
  </main>;
}
