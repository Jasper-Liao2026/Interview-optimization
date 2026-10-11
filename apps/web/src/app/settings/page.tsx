"use client";

import { useState } from "react";

import { TopNav } from "@/components/top-nav";
import { ApiError, api, newRequestId, type DemoImportResponse, type LocalDataClearResponse } from "@/lib/api-client";

export default function SettingsPage() {
  const [busy, setBusy] = useState<"demo" | "export" | "clear" | null>(null);
  const [message, setMessage] = useState<string>("");
  const [clearResult, setClearResult] = useState<LocalDataClearResponse | null>(null);

  async function importDemo() {
    setBusy("demo"); setMessage("");
    try {
      const result: DemoImportResponse = await api.importDemo({ requestId: newRequestId() });
      setMessage(result.message);
    } catch (error) { setMessage(error instanceof ApiError ? `导入失败（${error.status}）` : String(error)); }
    finally { setBusy(null); }
  }

  async function exportData() {
    setBusy("export"); setMessage("");
    try {
      const data = await api.exportLocalData({ requestId: newRequestId() });
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "resume-optimizer-data.json";
      anchor.click(); URL.revokeObjectURL(url); setMessage("已导出全部本地数据。");
    } catch (error) { setMessage(error instanceof ApiError ? `导出失败（${error.status}）` : String(error)); }
    finally { setBusy(null); }
  }

  async function clearData() {
    if (!window.confirm("这会删除本机用户数据、评测数据和检查点，且无法撤销。确定继续吗？")) return;
    setBusy("clear"); setMessage("");
    try {
      const result = await api.clearLocalData({ confirmation: "清空本地数据" }, { requestId: newRequestId() });
      setClearResult(result); setMessage("本地数据已清空。");
    } catch (error) { setMessage(error instanceof ApiError ? `清空失败（${error.status}）` : String(error)); }
    finally { setBusy(null); }
  }

  return (
    <main className="mx-auto w-full max-w-5xl px-6 py-10">
      <TopNav />
      <header><p className="font-mono text-xs tracking-widest text-[var(--muted)] uppercase">本地数据</p><h1 className="mt-2 text-2xl font-semibold">设置与数据管理</h1></header>
      <section className="mt-8 grid gap-5 lg:grid-cols-2">
        <Panel title="无 key 演示数据" detail="导入固定的项目、实习和校园经历与示例 JD。导入不会调用模型，生成结果会标记为 stub 演示。">
          <button className={button} disabled={busy !== null} onClick={() => void importDemo()}>{busy === "demo" ? "导入中…" : "导入演示数据"}</button>
        </Panel>
        <Panel title="备份本地数据" detail="下载 JSON 快照，包含当前用户数据、评测数据和 LangGraph 检查点。">
          <button className={button} disabled={busy !== null} onClick={() => void exportData()}>{busy === "export" ? "导出中…" : "导出全部数据 JSON"}</button>
        </Panel>
        <Panel title="清空本地数据" detail="删除当前用户数据、评测数据和检查点；数据库结构、服务元数据和 prompt 目录会保留。">
          <button className={`${button} border-[var(--err)] text-[var(--err)]`} disabled={busy !== null} onClick={() => void clearData()}>{busy === "clear" ? "清空中…" : "清空本地数据"}</button>
          {clearResult ? <p className="mt-3 text-xs text-[var(--muted)]">删除记录：{Object.values(clearResult.deleted_rows ?? {}).reduce((a, b) => a + b, 0)} 条。外部 Langfuse 与模型服务日志不会被此操作删除。</p> : null}
        </Panel>
      </section>
      {message ? <p className="mt-6 rounded-md border border-[var(--border)] bg-[var(--panel)] px-4 py-3 text-sm">{message}</p> : null}
    </main>
  );
}

const button = "rounded-md border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm hover:bg-[var(--panel-2)] disabled:cursor-not-allowed disabled:opacity-50";

function Panel({ title, detail, children }: { title: string; detail: string; children: React.ReactNode }) {
  return <section className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-5"><h2 className="font-medium">{title}</h2><p className="mt-2 text-sm leading-6 text-[var(--muted)]">{detail}</p><div className="mt-4">{children}</div></section>;
}
