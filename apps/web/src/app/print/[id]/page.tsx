"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { fetchResumeHtml } from "@/lib/api-client";

/**
 * M1-6 的「方案 C · 浏览器原生打印」入口。
 *
 * ## 做法：把服务端渲染的 HTML 变成**顶层文档**，再 `window.print()`
 *
 * 最初的实现是把 HTML 塞进 `srcDoc` iframe、调用 `window.print()` 打印外层页面。
 * 实测**打出空白首页**（3 页，第 1 页 0 字符），原因有两层叠加：
 *
 *   1. iframe 文档里的 `@page { size: A4; margin: 14mm 16mm }` **不会作用于外层的打印任务**
 *      —— `@page` 只对「被打印的那个文档」生效，而这里被打印的是父文档；
 *   2. 模板在 `@media print` 下又把 `.page` 的内边距清成了 0（因为它假设边距由 `@page` 提供）。
 *
 * 于是内容贴着纸边、外层还要再补一层 padding，两层一叠就出现溢出与空白页。
 * 与其用 padding 去「补偿」，不如把简历 HTML 直接写成顶层文档 ——
 * 那么它自带的 `@page` 与 `@media print` 就都作用在正确的文档上，
 * 输出与方案 B（服务端打印同一份 HTML）**结构上必然一致**。
 *
 * ## 为什么能这么干
 *
 * 后端 `GET /api/v1/resumes/{id}/html` 返回的是**一份完整 HTML 文档**，
 * 设计上就是「预览与 PDF 的同一份来源」。这里不过是把它换到顶层来渲染而已。
 *
 * 代价：本页的 React 树会被这行 `document.write` 抹掉。
 * 对「只用来自动打印、不需要再交互」的路由来说这是可接受的交换；
 * 真正需要留在屏幕上的预览在 `/generate` 页里，用的是 iframe。
 */
export default function PrintPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const controller = new AbortController();

    fetchResumeHtml(id, controller.signal)
      .then(async (html) => {
        setReady(true);
        // 抹掉当前文档，换成简历文档本身
        document.open();
        document.write(html);
        document.close();

        // 等字体与布局就绪再唤起打印，否则会打出未套用字体的内容
        try {
          await document.fonts?.ready;
        } catch {
          /* 字体接口不可用时直接继续 */
        }
        window.setTimeout(() => window.print(), 120);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(String(cause));
      });

    return () => controller.abort();
  }, [id]);

  // 走到这一步说明 HTML 已经写进文档了，这棵树即将被替换
  if (ready) return null;

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col justify-center gap-4 px-6">
      <p className="font-mono text-xs tracking-widest text-[var(--muted)] uppercase">
        浏览器打印 · 方案 C
      </p>
      {error ? (
        <>
          <p className="text-sm text-[var(--err)]">读取简历失败：{error}</p>
          <Link
            href="/generate"
            className="w-fit rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-1.5 text-xs hover:bg-[var(--border)]"
          >
            返回生成页
          </Link>
        </>
      ) : (
        <p className="text-sm text-[var(--muted)]">正在准备打印…</p>
      )}
    </main>
  );
}
