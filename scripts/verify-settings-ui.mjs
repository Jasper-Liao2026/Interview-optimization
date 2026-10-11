// Real Edge acceptance for the settings workflow. API calls are mocked so this
// script never imports or clears the local database.
import assert from "node:assert/strict";
import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { paths } from "./lib/paths.mjs";

const web = process.env.RESUME_WEB_URL || "http://127.0.0.1:3000";
const driver = process.env.RESUME_PLAYWRIGHT_MODULE || join(
  paths.repoRoot,
  "scripts/.tools/browser/node_modules/playwright/index.mjs",
);
if (!existsSync(driver)) throw new Error("Install the optional Playwright driver first.");
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ channel: "msedge", headless: true });
const outputDir = join(paths.repoRoot, "docs", "images");
mkdirSync(outputDir, { recursive: true });
const origin = new URL(web).origin;
let checks = 0;
let clearCalls = 0;
const errors = [];
let acceptNextClear = false;
function pass(label) { checks += 1; console.log(`PASS ${label}`); }

try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", async dialog => {
    // The first clear attempt is cancelled; the second is accepted below.
    if (acceptNextClear) {
      assert.equal(dialog.type(), "confirm");
      assert(dialog.message().includes("无法撤销"));
      acceptNextClear = false;
      await dialog.accept();
    } else {
      await dialog.dismiss();
    }
  });
  await context.route("**/api/v1/**", async route => {
    const request = route.request();
    const headers = {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      "Access-Control-Allow-Headers": "content-type,x-request-id",
    };
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/system/demo") && request.method() === "POST") {
      return route.fulfill({ json: {
        experience_ids: ["demo-1", "demo-2", "demo-3"],
        jd_id: "demo-jd",
        inserted_experiences: 3,
        inserted_jobs: 1,
        message: "已导入合成演示素材和岗位；不覆盖已有内容，请勿用于正式投递。",
      }, headers });
    }
    if (path.endsWith("/system/data/export") && request.method() === "GET") {
      return route.fulfill({ json: {
        exported_at: "2026-10-11T00:00:00Z", user_id: "local-user",
        tables: { experiences: [{ id: "demo-1" }], checkpoint_blobs: [] },
        preserved_tables: ["service_meta"],
        external_stores: ["Langfuse traces are separate; export or delete them in the configured project."],
      }, headers });
    }
    if (path.endsWith("/system/data/clear") && request.method() === "POST") {
      clearCalls += 1;
      assert.deepEqual(request.postDataJSON(), { confirmation: "清空本地数据" });
      return route.fulfill({ json: {
        cleared_at: "2026-10-11T00:00:00Z", deleted_rows: { experiences: 3, job_descriptions: 1 },
        preserved_tables: ["service_meta"],
        external_stores: ["Model providers may retain request logs; this operation cannot delete provider logs."],
      }, headers });
    }
    return route.fulfill({ status: 404, json: { detail: "unexpected request" }, headers });
  });

  await page.goto(`${web}/settings`);
  await page.getByRole("heading", { name: "设置与数据管理" }).waitFor();
  await page.locator("main").screenshot({ path: join(outputDir, "settings-demo.png") });
  pass("settings page renders in real Edge");

  await page.getByRole("button", { name: "导入演示数据", exact: true }).click();
  await page.getByText("已导入合成演示素材和岗位", { exact: false }).waitFor();
  pass("demo import displays synthetic-data boundary");

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出全部数据 JSON", exact: true }).click();
  const download = await downloadPromise;
  assert.equal(download.suggestedFilename(), "resume-optimizer-data.json");
  const chunks = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk);
  const exported = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  assert.deepEqual(exported.tables.experiences, [{ id: "demo-1" }]);
  assert(exported.external_stores[0].includes("Langfuse"));
  pass("data export downloads JSON");

  await page.getByRole("button", { name: "清空本地数据", exact: true }).click();
  await page.waitForTimeout(100);
  assert.equal(clearCalls, 0);
  pass("cancelled clear confirmation does not call API");

  acceptNextClear = true;
  await page.getByRole("button", { name: "清空本地数据", exact: true }).click();
  await page.getByText("本地数据已清空。", { exact: true }).waitFor();
  assert.equal(clearCalls, 1);
  pass("accepted clear confirmation calls API and reports result");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  assert.deepEqual(errors, []);
  pass("390px settings layout has no horizontal overflow or browser errors");
  console.log(`Settings UI: ${checks} checks passed`);
} finally {
  await browser.close();
}
