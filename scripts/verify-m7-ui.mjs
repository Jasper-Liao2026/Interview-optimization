// Optional driver: npm install --prefix scripts/.tools/browser playwright
// Reuses installed Edge. Start web/API before running; both URLs are configurable.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { paths } from "./lib/paths.mjs";
import { venvPython } from "./lib/proc.mjs";

const arg = (name, fallback) => {
  const index = process.argv.indexOf(name);
  return index < 0 ? fallback : process.argv[index + 1];
};
const web = arg("--web-url", "http://127.0.0.1:3107");
const backend = arg("--api-url", "http://127.0.0.1:8107");
const driver = process.env.RESUME_PLAYWRIGHT_MODULE || join(paths.repoRoot, "scripts/.tools/browser/node_modules/playwright/index.mjs");
if (!existsSync(driver)) throw new Error("Install optional Playwright driver in scripts/.tools/browser; see README.");
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ channel: "msedge", headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
await context.route("**/api/v1/**", async route => {
  const request = route.request();
  const cors = { "Access-Control-Allow-Origin": new URL(web).origin, "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS", "Access-Control-Allow-Headers": "content-type,x-request-id" };
  if (request.method() === "OPTIONS") { await route.fulfill({ status: 204, headers: cors }); return; }
  const url = new URL(request.url());
  const response = await route.fetch({ url: `${backend}${url.pathname}${url.search}`, timeout: 180_000 });
  await route.fulfill({ response, headers: { ...response.headers(), ...cors } });
});
const fixtures = { experiences: [], jobs: [], resumes: [], runs: [] };
const tag = `M7-UI-${Date.now()}`;
let checks = 0;
function pass(label) { checks++; console.log(`PASS ${label}`); }
async function call(method, path, body) {
  const response = await fetch(`${backend}/api/v1${path}`, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const text = await response.text();
  assert(response.ok, text);
  return JSON.parse(text);
}
try {
  const source = await call("POST", "/experiences", { kind: "project", org: tag, role: "开发", raw_description: "使用 Python FastAPI 开发后端接口与测试，维护 PDF 导出。", skill_tags: ["Python", "FastAPI"] });
  fixtures.experiences.push(source.id);
  for (const suffix of ["A", "B"]) {
    const job = await call("POST", "/jd/parse", { raw_text: "后端开发工程师，熟悉 Python FastAPI 接口开发与测试。", title: `${tag}-${suffix}`, company: tag, persist: true });
    fixtures.jobs.push(job.jd_id);
  }
  await page.goto(`${web}/export`);
  await page.getByRole("heading", { name: "批量生成与导出" }).waitFor();
  for (const suffix of ["A", "B"]) await page.getByRole("checkbox", { name: `${tag} · ${tag}-${suffix}`, exact: true }).check();
  await page.getByRole("checkbox", { name: `${tag} · 开发`, exact: true }).check();
  const responsePromise = page.waitForResponse(r => r.url().includes("/resumes/batch-generate") && r.request().method() === "POST", { timeout: 180_000 });
  await page.getByRole("button", { name: "生成所选岗位简历", exact: true }).click();
  const response = await responsePromise;
  const batch = await response.json();
  for (const item of batch.items) { fixtures.runs.push(item.run_id); if (item.resume) fixtures.resumes.push(item.resume.id); }
  assert.deepEqual(batch.items.map(item => item.status), ["completed", "completed"]);
  await page.getByRole("button", { name: "重试此批", exact: true }).waitFor();
  await page.getByLabel("筛选简历关键词").fill(tag);
  assert.equal(await page.getByRole("button", { name: "预览", exact: true }).count(), 2);
  pass("multi-JD UI generation and keyword filtering");
  await page.getByLabel("导出模板").selectOption("modern");
  await page.getByRole("button", { name: "预览", exact: true }).first().click();
  await page.getByLabel("PDF 第 1 页", { exact: true }).waitFor({ timeout: 90_000 });
  const pdfLink = page.getByRole("link", { name: "下载此 PDF", exact: true });
  const blobUrl = await pdfLink.getAttribute("href");
  assert(blobUrl.startsWith("blob:"));
  const bytes = await page.evaluate(async url => Array.from(new Uint8Array(await (await fetch(url)).arrayBuffer())), blobUrl);
  const downloadedPromise = page.waitForEvent("download");
  await pdfLink.click();
  const downloaded = await downloadedPromise;
  const chunks = [];
  for await (const chunk of await downloaded.createReadStream()) chunks.push(chunk);
  assert.deepEqual(Array.from(Buffer.concat(chunks)), bytes);
  pass("modern PDF.js preview and downloaded bytes identical");
  await page.getByLabel("导出模板").selectOption("classic");
  assert.equal(await pdfLink.count(), 0);
  pass("template switch invalidates stale PDF download");
  await page.getByRole("button", { name: "勾选筛选结果", exact: false }).click();
  const zipPromise = page.waitForEvent("download", { timeout: 90_000 });
  await page.getByRole("button", { name: "下载所选 2 份简历 ZIP", exact: false }).click();
  const zip = await zipPromise;
  const zipped = [];
  for await (const chunk of await zip.createReadStream()) zipped.push(chunk);
  assert.equal(Buffer.concat(zipped).subarray(0, 2).toString(), "PK");
  pass("filtered selection downloads actual ZIP");
  await page.getByLabel("按岗位筛选简历").selectOption(fixtures.jobs[0]);
  assert.equal(await page.getByRole("button", { name: "预览", exact: true }).count(), 1);
  pass("JD filter narrows resumes");
  const editor = await call("GET", `/resumes/${fixtures.resumes[0]}/editor`);
  const editedTitle = `${tag}-人工修改`;
  await call("PUT", `/resumes/${fixtures.resumes[0]}/editor`, {
    title: editedTitle, header: editor.resume.header, sections: editor.resume.sections,
    expected_revision: editor.revision,
  });
  await page.reload();
  await page.getByText("已恢复上次批次。", { exact: false }).waitFor();
  const retryPromise = page.waitForResponse(r => r.url().includes("/resumes/batch-generate") && r.request().method() === "POST", { timeout: 180_000 });
  await page.getByRole("button", { name: "重试此批", exact: true }).click();
  const retried = await (await retryPromise).json();
  assert.equal(retried.batch_id, batch.batch_id);
  assert.deepEqual(retried.items.map(item => item.resume.id), fixtures.resumes);
  await page.getByText("简历列表已刷新为当前编辑内容。", { exact: false }).waitFor();
  pass("refresh restores same batch without duplicate resumes");
  await page.getByLabel("筛选简历关键词").fill(editedTitle);
  assert.equal(await page.getByRole("button", { name: "预览", exact: true }).count(), 1);
  await page.getByRole("checkbox", { name: `选择 ${editedTitle}`, exact: true }).waitFor();
  pass("batch retry keeps manually edited resume in export list");
  await page.getByLabel("筛选简历关键词").fill(tag);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  pass("mobile export page has no horizontal overflow");
  assert.deepEqual(errors, []);
  pass("no browser page errors");
} finally {
  await browser.close();
  const cleanup = spawnSync(venvPython(paths.apiVenv), ["-c", `
import asyncio,json,sys
from uuid import UUID
from app.config import Settings
from app.db import Database
async def cleanup():
    data=json.load(sys.stdin)
    db=Database(Settings())
    try:
        async with db.connection() as conn:
            for table,key in (("resumes","resumes"),("generation_runs","runs"),("job_descriptions","jobs"),("experiences","experiences")):
                await conn.execute(f"delete from {table} where id=any($1::uuid[])",[UUID(i) for i in data[key]])
            for table in ("checkpoint_writes","checkpoint_blobs","checkpoints"):
                await conn.execute(f"delete from {table} where thread_id=any($1::text[])",data["runs"])
    finally:
        await db.close()
if sys.platform=="win32": asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(cleanup())
`], { cwd: paths.apiDir, input: JSON.stringify(fixtures), encoding: "utf8", windowsHide: true });
  if (cleanup.status !== 0) throw new Error(`Fixture cleanup failed: ${cleanup.stderr}`);
}
console.log(`M7 UI: ${checks} real browser/API checks passed; model explicitly stub`);
