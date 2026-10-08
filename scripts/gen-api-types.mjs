/**
 * M0-6 · 类型生成管线
 *
 *   Pydantic model ──FastAPI──> openapi.json ──openapi-typescript──> schema.d.ts
 *
 * 单一方向、可重复执行。改完 Pydantic model 只需跑 `pnpm gen:types`，
 * 前端类型自动刷新 —— 这条约定是方案 A（前端壳 + Python 全包）不崩的前提。
 *
 * 用法：
 *   node scripts/gen-api-types.mjs                  # 全流程
 *   node scripts/gen-api-types.mjs --only-openapi   # 只导出 openapi.json
 *   node scripts/gen-api-types.mjs --check          # CI 用：生成前后比对，有漂移则失败
 */
import { existsSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

import { run, venvPython } from "./lib/proc.mjs";
import { paths } from "./lib/paths.mjs";

const args = new Set(process.argv.slice(2));
const onlyOpenapi = args.has("--only-openapi");
const checkMode = args.has("--check");

/** --check 模式：生成前存一份内容，生成后比对 */
function snapshot(file) {
  return existsSync(file) ? readFileSync(file, "utf8") : null;
}

const before = checkMode ? snapshot(paths.typesFile) : null;

/* ---------------------------------------------------- 1. openapi.json */
const python = venvPython(paths.apiVenv);
if (!existsSync(python)) {
  console.error(`找不到后端虚拟环境：${python}\n先执行：pnpm api:setup`);
  process.exit(1);
}

console.log("[1/2] 导出 OpenAPI schema（直接 import app，不需要起服务）");
run(python, ["scripts/export_openapi.py"], { cwd: paths.apiDir });

if (!existsSync(paths.openapiJson)) {
  console.error(`导出失败，未生成：${paths.openapiJson}`);
  process.exit(1);
}

if (onlyOpenapi) {
  console.log("[done] 只导出 openapi.json，跳过类型生成");
  process.exit(0);
}

/* ------------------------------------------------------ 2. schema.d.ts */
console.log("[2/2] 生成 TypeScript 类型");

// 直接用 node 跑 CLI，而不是 spawn `pnpm gen`：
// Windows 下 pnpm 是 .cmd，spawn 不经过 shell 会失败；绕开 shell 也就绕开了引号与编码问题。
const cliCandidates = [
  join(paths.typesPackage, "node_modules", "openapi-typescript", "bin", "cli.js"),
  join(paths.repoRoot, "node_modules", "openapi-typescript", "bin", "cli.js"),
];
const cli = cliCandidates.find((candidate) => existsSync(candidate));

if (!cli) {
  console.error(
    "找不到 openapi-typescript。先执行 pnpm install（依赖安装在 packages/api-types/node_modules）。",
  );
  process.exit(1);
}

// ⚠️ 必须传**相对路径**，不能用绝对路径。
// openapi-typescript 内部经 @redocly/openapi-core 解析输入，它把传入的字符串当 URL 处理，
// 于是非 ASCII 的绝对路径会被百分号编码成一个并不存在的路径：
//   ENOENT: 'c:\Users\liaoh\Desktop\%E9%9D%A2%E8%AF%95%E4%BC%98%E5%8C%96%E5%99%A8\apps\api\openapi\openapi.json'
// （%E9%9D%A2… 正是「面试优化器」的 UTF-8 编码）
// 相对路径只含 ASCII，编码后与原样一致，因此可以正常工作。
// cwd 固定为 packages/api-types，保证相对路径的基准稳定。
const relativeInput = relative(paths.typesPackage, paths.openapiJson);
const relativeOutput = relative(paths.typesPackage, paths.typesFile);

run(process.execPath, [cli, relativeInput, "-o", relativeOutput], { cwd: paths.typesPackage });
console.log(`[done] 类型已刷新：${paths.typesFile}`);

if (checkMode) {
  const after = snapshot(paths.typesFile);
  if (before !== after) {
    console.error(
      "\n[check] ✗ 类型与 Pydantic model 不同步。\n" +
        "        有人改了 apps/api/app/schemas/ 却没提交刷新后的 schema.d.ts。\n" +
        "        修复：pnpm gen:types，然后提交 packages/api-types/src/schema.d.ts",
    );
    process.exit(1);
  }
  console.log("[check] ✓ 类型与后端 schema 同步");
}
