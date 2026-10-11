// Optional driver: npm install --prefix scripts/.tools/browser playwright
// Exercises the production page with offline API responses and a real clipboard.
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { paths } from "./lib/paths.mjs";

const web = process.env.RESUME_WEB_URL || "http://127.0.0.1:3000";
const driver = process.env.RESUME_PLAYWRIGHT_MODULE || join(paths.repoRoot, "scripts/.tools/browser/node_modules/playwright/index.mjs");
if (!existsSync(driver)) throw new Error("Install optional Playwright driver in scripts/.tools/browser; see README.");
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ channel: "msedge", headless: true });
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64");
const submissions = [];
const errors = [];
let checks = 0;
function pass(label) { checks++; console.log(`PASS ${label}`); }
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: new URL(web).origin });
  const page = await context.newPage();
  page.on("pageerror", error => errors.push(error.message));
  await context.route("**/api/v1/**", async route => {
    const request = route.request();
    const headers = { "Access-Control-Allow-Origin": new URL(web).origin, "Access-Control-Allow-Methods": "GET,POST,OPTIONS", "Access-Control-Allow-Headers": "content-type,x-request-id" };
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (new URL(request.url()).pathname.endsWith("/experiences")) {
      return route.fulfill({ json: { items: [{ id: "00000000-0000-4000-8000-000000000001", kind: "project", org: "输入验证", role: "开发", raw_description: "Python 后端开发", skill_tags: [] }], total: 1 }, headers });
    }
    if (new URL(request.url()).pathname.endsWith("/resumes/generate")) {
      submissions.push(request.postDataJSON());
      // Stop before generation; this test checks the submitted input without writing data.
      return route.fulfill({ status: 422, json: { detail: "UI input verification" }, headers });
    }
    return route.fulfill({ status: 404, json: {}, headers });
  });
  await page.goto(`${web}/generate`);
  const text = page.getByRole("textbox", { name: "JD 原文", exact: true });
  await text.fill("招聘后端工程师，熟悉 Python 开发。");
  await page.getByLabel("上传 JD 截图").setInputFiles({ name: "岗位.png", mimeType: "image/png", buffer: png });
  await page.getByAltText("待解析的岗位截图").waitFor();
  assert.equal(await text.inputValue(), "招聘后端工程师，熟悉 Python 开发。");
  await text.press("End");
  await text.pressSequentially("补充岗位信息");
  assert((await text.inputValue()).includes("补充岗位信息"));
  assert.equal(await page.getByAltText("待解析的岗位截图").count(), 1);
  pass("upload preserves text and typing preserves attachment");

  await text.fill("关注后端");
  await page.getByRole("button", { name: "生成简历", exact: true }).click();
  await page.getByText(/UI input verification/).waitFor();
  assert.equal(submissions[0].jd_text, "关注后端");
  assert(submissions[0].jd_image_data_url.startsWith("data:image/png;base64,"));
  assert.equal(submissions[0].jd_id, null);
  pass("short supplement and screenshot submitted together");

  await page.getByRole("button", { name: "移除截图", exact: true }).click();
  assert.equal(await text.inputValue(), "关注后端");
  assert(await text.evaluate(element => element === document.activeElement));
  await text.pressSequentially("，熟悉数据库开发。");
  assert.equal(await page.getByAltText("待解析的岗位截图").count(), 0);
  const textOnly = await text.inputValue();
  const textResponse = page.waitForResponse(response => response.url().endsWith("/resumes/generate") && response.request().method() === "POST");
  await page.getByRole("button", { name: "生成简历", exact: true }).click();
  await textResponse;
  assert.equal(submissions[1].jd_text, textOnly);
  assert.equal(submissions[1].jd_image_data_url, null);
  pass("removing screenshot preserves text, focus, typing and text submission");

  await text.fill("");
  await page.evaluate(async bytes => {
    await navigator.clipboard.write([new ClipboardItem({ "image/png": new Blob([new Uint8Array(bytes)], { type: "image/png" }) })]);
  }, [...png]);
  await text.focus();
  await page.keyboard.press("Control+v");
  await page.getByAltText("待解析的岗位截图").waitFor();
  await text.pressSequentially("截图补充");
  assert.equal(await text.inputValue(), "截图补充");
  await page.getByRole("button", { name: "移除截图", exact: true }).click();
  await text.pressSequentially("继续编辑");
  assert((await text.inputValue()).includes("继续编辑"));
  pass("real Ctrl+V clipboard supports typing before and after removal");

  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(errors, []);
  pass("mobile layout and no browser errors");
  console.log(`${checks} checks passed`);
} finally {
  await browser.close();
}
