"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { TopNav } from "@/components/top-nav";
import {
  api, ApiError, newRequestId,
  type JdRead, type JobProfile, type MatchResponse, type JdParseResponse,
} from "@/lib/api-client";

const panel = "rounded-xl border border-[var(--border)] bg-[var(--panel)] p-5";
const input = "w-full rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent)]";
const button = "rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2 text-xs hover:bg-[var(--border)] disabled:cursor-not-allowed disabled:opacity-40";
const statusLabels = { covered: "已覆盖", related: "相关待补证", missing: "缺失" };
const statusColors = { covered: "var(--ok)", related: "var(--warn)", missing: "var(--err)" };
const categoryLabels = { required: "必备技能", preferred: "加分项", responsibility: "职责", domain: "业务域" };

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? `请求失败（${error.status}）：${error.body.slice(0, 300)}` : String(error);
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<JdRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [active, setActive] = useState<JdRead | null>(null);
  const [mode, setMode] = useState<"text" | "image">("text");
  const [rawText, setRawText] = useState("");
  const [image, setImage] = useState<string | null>(null);
  const [imageName, setImageName] = useState("");
  const [readingImage, setReadingImage] = useState(false);
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [editCompany, setEditCompany] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parseInfo, setParseInfo] = useState<JdParseResponse | null>(null);
  const [savedRecovery, setSavedRecovery] = useState<JdParseResponse | null>(null);
  const [match, setMatch] = useState<MatchResponse | null>(null);
  const [matching, setMatching] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const matchController = useRef<AbortController | null>(null);
  const fileVersion = useRef(0);
  const actionController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api.listJobs({ signal: controller.signal, requestId: newRequestId() })
      .then(data => setJobs(data.items))
      .catch((err: unknown) => { if (!controller.signal.aborted) setListError(errorMessage(err)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => {
      controller.abort();
      matchController.current?.abort();
      actionController.current?.abort();
      fileVersion.current += 1;
    };
  }, []);

  function choose(job: JdRead | null) {
    matchController.current?.abort();
    setActive(job);
    setEditTitle(job?.title ?? "");
    setEditCompany(job?.company ?? "");
    setMatch(null);
    setMatching(false);
    setSelected(new Set());
    setParseInfo(null);
    setError(null);
  }

  async function readImage(file?: File) {
    const version = ++fileVersion.current;
    setImage(null);
    setImageName("");
    setError(null);
    setReadingImage(false);
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      setError("仅支持 PNG、JPEG 或 WebP 截图。");
      return;
    }
    if (file.size === 0 || file.size > 5 * 1024 * 1024) {
      setError("截图必须非空，且不超过 5 MiB。");
      return;
    }
    setReadingImage(true);
    try {
      const bitmap = await createImageBitmap(file);
      const pixels = bitmap.width * bitmap.height;
      bitmap.close();
      if (pixels > 20_000_000) throw new Error("截图不能超过 2000 万像素。");
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error("截图读取失败，请重新选择文件。"));
        reader.readAsDataURL(file);
      });
      if (version !== fileVersion.current) return;
      setImage(dataUrl);
      setImageName(file.name);
    } catch (err) {
      if (version === fileVersion.current) setError(errorMessage(err));
    } finally {
      if (version === fileVersion.current) setReadingImage(false);
    }
  }

  async function parse() {
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    actionController.current = controller;
    const opts = { requestId: newRequestId(), signal: controller.signal };
    try {
      const metadata = { title: title.trim() || null, company: company.trim() || null, persist: true };
      const response = mode === "image" && image
        ? await api.parseJobImage({ ...metadata, image_data_url: image }, opts)
        : await api.parseJob({ ...metadata, raw_text: rawText.trim() }, opts);
      if (!response.jd_id) throw new Error("解析结果未保存，请重试。");
      if (controller.signal.aborted) return;
      // POST 已经落库。后续读取失败只能重试 GET，不能把保存当作失败再提交。
      setSavedRecovery(response);
      setRawText("");
      setTitle("");
      setCompany("");
      setImage(null);
      setImageName("");
      await readSavedJob(response, controller);
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function readSavedJob(response: JdParseResponse, controller: AbortController) {
    if (!response.jd_id) return;
    const opts = { requestId: newRequestId(), signal: controller.signal };
    try {
      const job = await api.getJob(response.jd_id, opts);
      if (controller.signal.aborted) return;
      setJobs(prev => [job, ...prev.filter(item => item.id !== job.id)]);
      choose(job);
      setParseInfo(response);
      setSavedRecovery(null);
      setListError(null);
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(`岗位已保存，详情暂时读取失败。请点击「读取已保存岗位」恢复，无需重新解析。${errorMessage(err)}`);
      }
      return;
    }
    try {
      const list = await api.listJobs(opts);
      if (!controller.signal.aborted) setJobs(list.items);
    } catch (err) {
      if (!controller.signal.aborted) {
        setListError(`岗位已保存并可选用，完整列表暂时刷新失败；重新加载页面可重试。${errorMessage(err)}`);
      }
    }
  }

  async function recoverSavedJob() {
    if (!savedRecovery) return;
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    actionController.current = controller;
    try {
      await readSavedJob(savedRecovery, controller);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function update() {
    if (!active) return;
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    actionController.current = controller;
    try {
      const job = await api.updateJob(active.id, { title: editTitle.trim() || null, company: editCompany.trim() || null }, { requestId: newRequestId(), signal: controller.signal });
      if (controller.signal.aborted) return;
      setActive(job);
      setJobs(prev => prev.map(item => item.id === job.id ? job : item));
    } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }

  async function remove() {
    if (!active || !window.confirm(`确认删除「${active.title || "未命名岗位"}」？`)) return;
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    actionController.current = controller;
    try {
      await api.deleteJob(active.id, { requestId: newRequestId(), signal: controller.signal });
      if (controller.signal.aborted) return;
      setJobs(prev => prev.filter(item => item.id !== active.id));
      choose(null);
    } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }

  async function runMatch() {
    if (!active) return;
    matchController.current?.abort();
    const controller = new AbortController();
    matchController.current = controller;
    setMatching(true);
    setMatch(null);
    setSelected(new Set());
    setError(null);
    try {
      const data = await api.matchJob(active.id, { candidate_limit: 20 }, { requestId: newRequestId(), signal: controller.signal });
      if (controller.signal.aborted) return;
      setMatch(data);
      setSelected(new Set(data.items.filter(item => item.matches.some(cell => cell.status === "covered")).map(item => item.experience_id)));
    } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)); }
    finally { if (!controller.signal.aborted) setMatching(false); }
  }

  const canParse = !loading && !busy && !savedRecovery && !readingImage && (mode === "image" ? !!image : rawText.trim().length >= 10 && rawText.trim().length <= 20000);
  const generateUrl = active ? `/generate?jd=${encodeURIComponent(active.id)}${match ? `&experiences=${encodeURIComponent([...selected].join(","))}` : ""}` : "/generate";

  return (
    <main className="mx-auto w-full max-w-7xl px-6 py-8">
      <TopNav />
      <header className="mb-6">
        <p className="font-mono text-xs tracking-widest text-[var(--muted)]">M3 · 岗位匹配</p>
        <h1 className="mt-2 text-2xl font-semibold">岗位画像与经历匹配</h1>
        <p className="mt-2 text-sm text-[var(--muted)]">保存岗位，逐项查看经历的事实证据与缺口，再选择经历生成简历。</p>
      </header>
      {error ? <p role="alert" className="mb-5 rounded-md border border-[var(--err)]/40 p-3 text-xs break-all text-[var(--err)]">{error}</p> : null}
      {savedRecovery ? <div role="status" className="mb-5 rounded-md border border-[var(--warn)]/40 p-3 text-xs"><p>岗位已保存（{savedRecovery.jd_id}），正在等待详情读取。请勿重复提交。</p><button type="button" disabled={busy} className={`${button} mt-2`} onClick={() => void recoverSavedJob()}>读取已保存岗位</button></div> : null}
      <div className="grid gap-5 lg:grid-cols-[340px_1fr]">
        <aside className="space-y-5">
          <section className={panel}>
            <h2 className="mb-3 text-sm font-medium">添加岗位</h2>
            <div className="mb-3 flex gap-2">
              <button type="button" disabled={busy} aria-pressed={mode === "text"} onClick={() => setMode("text")} className={button}>粘贴文本</button>
              <button type="button" disabled={busy} aria-pressed={mode === "image"} onClick={() => setMode("image")} className={button}>上传截图</button>
            </div>
            <div className="space-y-3">
              <label className="block text-xs text-[var(--muted)]">岗位标题（可选）<input className={`${input} mt-1`} value={title} maxLength={120} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
              <label className="block text-xs text-[var(--muted)]">公司（可选）<input className={`${input} mt-1`} value={company} maxLength={120} disabled={busy} onChange={event => setCompany(event.target.value)} /></label>
              {mode === "text" ? <label className="block text-xs text-[var(--muted)]">JD 原文<textarea className={`${input} mt-1`} rows={10} value={rawText} maxLength={20000} disabled={busy} onChange={event => setRawText(event.target.value)} placeholder="粘贴招聘说明，10–20000 字" /><span className="mt-1 block">{rawText.trim().length} / 20000 字</span></label> : <label className="block text-xs text-[var(--muted)]">岗位截图<input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} className="mt-2 block w-full text-xs" onChange={event => void readImage(event.target.files?.[0])} /><span className="mt-2 block">PNG / JPEG / WebP，≤5 MiB、≤2000 万像素</span>{readingImage ? <span className="mt-2 block">读取截图…</span> : imageName ? <span className="mt-2 block break-all">已选择：{imageName}</span> : null}</label>}
              <button type="button" className={`${button} w-full`} disabled={!canParse} onClick={() => void parse()}>{busy ? "处理中…" : "解析并保存岗位"}</button>
            </div>
          </section>
          <section className={panel}>
            <h2 className="mb-3 text-sm font-medium">已保存岗位（{jobs.length}）</h2>
            {listError ? <p role="alert" className="mb-3 text-xs text-[var(--err)]">{listError}</p> : null}
            {loading ? <p className="text-xs text-[var(--muted)]">读取岗位…</p> : jobs.length === 0 ? <p className="text-xs text-[var(--muted)]">还没有岗位，先粘贴文本或上传截图。</p> : <ul className="space-y-2">{jobs.map(job => <li key={job.id}><button type="button" disabled={busy} aria-pressed={active?.id === job.id} className={`${button} w-full text-left`} style={{ borderColor: active?.id === job.id ? "var(--accent)" : undefined }} onClick={() => choose(job)}><span className="block">{job.title || "未命名岗位"}</span><span className="mt-1 block text-[var(--muted)]">{job.company || "未填写公司"} · {job.source_type === "image" ? "截图" : "文本"}</span></button></li>)}</ul>}
          </section>
        </aside>
        <div className="space-y-5 min-w-0">
          {!active ? <section className={panel}><p className="text-sm text-[var(--muted)]">选择一个已保存岗位，查看画像和匹配结果。</p></section> : <>
            <section className={panel}>
              <h2 className="mb-3 text-sm font-medium">岗位信息</h2>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs text-[var(--muted)]">岗位标题<input value={editTitle} maxLength={120} disabled={busy} onChange={event => setEditTitle(event.target.value)} className={`${input} mt-1`} /></label>
                <label className="text-xs text-[var(--muted)]">公司<input value={editCompany} maxLength={120} disabled={busy} onChange={event => setEditCompany(event.target.value)} className={`${input} mt-1`} /></label>
              </div>
              <div className="mt-3 flex flex-wrap gap-2"><button type="button" disabled={busy} className={button} onClick={() => void update()}>保存标题与公司</button><button type="button" disabled={busy} className={`${button} text-[var(--err)]`} onClick={() => void remove()}>删除岗位</button><button type="button" disabled={busy || matching || !active.parsed} className={button} onClick={() => void runMatch()}>{matching ? "匹配中…" : "匹配素材库经历"}</button></div>
              <details className="mt-4 text-xs"><summary className="cursor-pointer text-[var(--muted)]">查看 JD 原文</summary><p className="mt-2 whitespace-pre-wrap leading-relaxed">{active.raw_text}</p></details>
              {parseInfo ? <div className="mt-3"><Notices isStub={parseInfo.is_stub} warnings={parseInfo.warnings} /><p className="mt-2 break-all font-mono text-[11px] text-[var(--muted)]">{parseInfo.provider} · {parseInfo.model} · trace_id: {parseInfo.trace_id}</p></div> : active.parser_model?.includes("stub") ? <Notices isStub warnings={[]} /> : null}
            </section>
            <section className={panel}><h2 className="mb-3 text-sm font-medium">结构化岗位画像</h2>{active.parsed ? <ProfileView profile={active.parsed} /> : <p className="text-xs text-[var(--warn)]">该岗位尚无结构化画像，请重新解析原文。</p>}</section>
            <section className={panel}>
              <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-sm font-medium">经历 × 岗位要求</h2>{(!match || selected.size > 0) ? <Link href={generateUrl} className={button}>用{match ? `所选 ${selected.size} 段经历` : "此岗位"}生成简历</Link> : <span className="text-xs text-[var(--muted)]">请选择至少一段经历</span>}</div>
              <p className="mt-2 text-xs leading-relaxed text-[var(--muted)]">规则分范围 0–100，用于比较素材与要求的匹配程度；向量相似只辅助候选召回，已覆盖需有事实证据，分数不代表录用概率。</p>
              {matching ? <p role="status" className="mt-4 text-xs text-[var(--muted)]">正在匹配经历，请稍候…</p> : !match ? <p className="mt-4 text-xs text-[var(--muted)]">点击「匹配素材库经历」查看分数、证据与技能缺口。</p> : <>
                <Notices isStub={match.is_stub} warnings={match.warnings} />
                <p className="mt-3 break-all font-mono text-[11px] text-[var(--muted)]">embedding: {match.embedding_model} · trace_id: {match.trace_id}</p>
                <div className="mt-4 rounded-md border border-[var(--warn)]/40 p-3 text-xs"><p className="font-medium text-[var(--warn)]">未被事实证据覆盖的要求</p>{match.uncovered_requirement_ids.length === 0 ? <p className="mt-1">所有要求均有覆盖证据。</p> : <ul className="mt-2 list-disc space-y-1 pl-4">{match.uncovered_requirement_ids.map(id => <li key={id}>{match.requirements.find(req => req.id === id)?.text ?? id}</li>)}</ul>}</div>
                <MatchMatrix
                  match={match}
                  selected={selected}
                  onToggle={id => setSelected(prev => {
                    const next = new Set(prev);
                    if (next.has(id)) next.delete(id);
                    else next.add(id);
                    return next;
                  })}
                />
              </>}
            </section>
          </>}
        </div>
      </div>
    </main>
  );
}

function Notices({ isStub, warnings }: { isStub: boolean; warnings: string[] }) {
  return <div className="mt-3 space-y-2 text-xs text-[var(--warn)]">{isStub ? <p className="rounded-md border border-[var(--warn)]/40 p-3">当前使用 stub 演示数据，结果仅用于验证流程，请勿据此判断真实匹配情况。</p> : null}{warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div>;
}

function MatchMatrix({ match, selected, onToggle }: {
  match: MatchResponse;
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (match.items.length === 0) {
    return <p className="mt-4 text-xs text-[var(--muted)]">素材库暂无可匹配经历，请到 <Link href="/library" className="underline">素材库</Link> 添加经历。</p>;
  }
  if (match.requirements.length === 0) {
    return <p className="mt-4 text-xs text-[var(--warn)]">岗位画像未提取到可匹配要求，请检查 JD 原文。</p>;
  }
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full border-collapse text-left text-xs">
        <caption className="sr-only">经历与岗位要求的匹配证据矩阵</caption>
        <thead>
          <tr>
            <th scope="col" className="min-w-44 border border-[var(--border)] p-3">经历 / 总分</th>
            {match.requirements.map(req => (
              <th scope="col" key={req.id} className="min-w-56 border border-[var(--border)] p-3 font-normal">
                <span className="block text-[var(--muted)]">{categoryLabels[req.category]} · 权重 {req.weight}</span>
                <span className="mt-1 block">{req.text}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {match.items.map(item => (
            <tr key={item.experience_id}>
              <th scope="row" className="border border-[var(--border)] p-3 align-top font-normal">
                <label className="flex items-start gap-2">
                  <input type="checkbox" checked={selected.has(item.experience_id)} onChange={() => onToggle(item.experience_id)} className="accent-[var(--accent)]" />
                  <span>
                    {item.org}
                    <span className="mt-1 block text-[var(--muted)]">{item.role}</span>
                    <span className="mt-2 block font-mono">总分 {item.score.toFixed(1)} / 100</span>
                  </span>
                </label>
              </th>
              {match.requirements.map(req => {
                const cell = item.matches.find(value => value.requirement_id === req.id);
                return (
                  <td key={req.id} className="border border-[var(--border)] p-3 align-top">
                    {cell ? <>
                      <p style={{ color: statusColors[cell.status] }}>{statusLabels[cell.status]} · {cell.score.toFixed(1)} 分</p>
                      <p className="mt-2 leading-relaxed text-[var(--muted)]">{cell.reason}</p>
                      {cell.evidence.length > 0 ? (
                        <ul className="mt-2 list-disc space-y-1 pl-4">
                          {cell.evidence.map((evidence, index) => <li key={index}>{evidence}</li>)}
                        </ul>
                      ) : <p className="mt-2 text-[var(--muted)]">暂无事实证据</p>}
                      {cell.semantic_similarity !== null ? (
                        <p className="mt-2 font-mono text-[10px] text-[var(--muted)]">
                          语义相似 {cell.semantic_similarity.toFixed(3)}{cell.shortlisted ? " · 召回候选" : ""}
                        </p>
                      ) : null}
                    </> : "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProfileView({ profile }: { profile: JobProfile }) {
  const rows = [
    ["必备技能", profile.required_skills], ["加分项", profile.nice_to_have],
    ["岗位职责", profile.responsibilities], ["关键词", profile.keywords],
    ["隐含偏好", profile.implicit_preferences],
  ] as const;
  return <div className="space-y-3 text-xs"><p className="text-[var(--muted)]">{profile.title || "未提取标题"} · {profile.company || "未提取公司"} · {profile.seniority || "未注明级别"}</p><p>业务域：{profile.business_domain || "未注明"}</p>{rows.map(([label, items]) => <div key={label}><p className="mb-1 text-[var(--muted)]">{label}</p>{items.length === 0 ? <span>未提取到条目</span> : <ul className="flex flex-wrap gap-2">{items.map((item, index) => <li key={index} className="rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-2 py-1">{item}</li>)}</ul>}</div>)}</div>;
}
