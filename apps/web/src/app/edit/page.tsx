"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { TopNav } from "@/components/top-nav";

export default function EditorLanding() {
  const router = useRouter();
  const [id, setId] = useState("");
  const [recentId, setRecentId] = useState<string | null>(null);
  useEffect(() => {
    try { setRecentId(localStorage.getItem("resume-optimizer:last-edited-resume")); } catch { /* Optional shortcut. */ }
  }, []);
  const valid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id.trim());
  return <main className="mx-auto max-w-3xl px-6 py-8"><TopNav /><h1 className="text-xl font-semibold">编辑简历</h1><p className="my-3 text-sm text-[var(--muted)]">在生成结果中点击「编辑简历」，或输入已有简历 ID。</p><form className="flex gap-2" onSubmit={event => { event.preventDefault(); if (valid) router.push(`/edit/${id.trim()}`); }}><input aria-label="简历 ID" value={id} onChange={event => setId(event.target.value)} placeholder="简历 UUID" className="min-w-0 flex-1 rounded-md border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm" /><button disabled={!valid} className="rounded-md bg-[var(--accent)] px-4 py-2 text-sm disabled:opacity-40">打开编辑器</button></form><div className="mt-5 flex gap-4 text-sm text-[var(--accent)]">{recentId && /^[0-9a-f-]{36}$/i.test(recentId) ? <Link href={`/edit/${recentId}`}>继续编辑最近的简历</Link> : null}<Link href="/generate">生成新简历</Link></div></main>;
}
