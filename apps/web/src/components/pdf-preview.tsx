"use client";

import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";

/** Render the exact downloadable PDF, sized to the containing panel. */
export function PdfPreview({ url }: { url: string }) {
  const host = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pages, setPages] = useState(0);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    let disposed = false;
    let doc: PDFDocumentProxy | null = null;
    let task: ReturnType<typeof import("pdfjs-dist")["getDocument"]> | null = null;
    let observer: ResizeObserver | null = null;
    let epoch = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const renders = new Set<RenderTask>();
    setError(null);
    setPages(0);
    element.replaceChildren();
    async function renderPages() {
      if (!doc || disposed || element!.clientWidth === 0) return;
      const current = ++epoch;
      for (const render of renders) render.cancel();
      renders.clear();
      const width = Math.max(100, element!.clientWidth);
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const fragment = document.createDocumentFragment();
      for (let number = 1; number <= doc.numPages; number++) {
        const page = await doc.getPage(number);
        if (disposed || current !== epoch) return;
        const natural = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: width / natural.width });
        const wrapper = document.createElement("div");
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = "100%";
        canvas.style.height = "auto";
        canvas.setAttribute("aria-label", `PDF 第 ${number} 页`);
        canvas.setAttribute("role", "img");
        wrapper.append(canvas);
        const label = document.createElement("p");
        label.className = "py-2 text-center text-[11px] text-[var(--muted)]";
        label.textContent = `${number} / ${doc.numPages}`;
        wrapper.append(label);
        fragment.append(wrapper);
        const render = page.render({ canvas, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
        renders.add(render);
        try { await render.promise; }
        catch (cause: unknown) {
          if (disposed || current !== epoch) return;
          throw cause;
        } finally { renders.delete(render); }
      }
      if (!disposed && current === epoch) element!.replaceChildren(fragment);
    }
    function draw() {
      void renderPages().catch((cause: unknown) => { if (!disposed) setError(`PDF 预览失败：${String(cause)}`); });
    }
    (async () => {
      const pdfjs = await import("pdfjs-dist");
      if (disposed) return;
      pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
      task = pdfjs.getDocument({ url });
      doc = await task.promise;
      if (disposed) { await task.destroy(); return; }
      setPages(doc.numPages);
      observer = new ResizeObserver(() => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(draw, 100);
      });
      observer.observe(element);
      draw();
    })().catch((cause: unknown) => { if (!disposed) setError(`PDF 预览失败：${String(cause)}`); });
    return () => {
      disposed = true;
      epoch++;
      observer?.disconnect();
      if (timer) clearTimeout(timer);
      for (const render of renders) render.cancel();
      void task?.destroy();
    };
  }, [url]);
  return <div>
    {error ? <p role="alert" className="mb-3 text-xs text-[var(--err)]">{error}</p> : null}
    {!pages && !error ? <p className="py-6 text-xs text-[var(--muted)]">正在加载 PDF 页面…</p> : null}
    <div ref={host} aria-label="当前简历 PDF 分页预览" className="max-h-[80vh] min-h-[300px] w-full space-y-3 overflow-y-auto overflow-x-hidden rounded-md" />
  </div>;
}
