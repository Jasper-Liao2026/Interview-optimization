/**
 * M0 验收脚本 —— 一条命令复现 M0-1 ~ M0-7 的验收结论。
 *
 *   node scripts/verify-m0.mjs
 *
 * 设计原则：**能自动判定的就自动判定，需要外部服务的就明确标记为 SKIP**，
 * 不用「看起来没问题」来充数。
 */
import { existsSync } from "node:fs";
import { join } from "node:path";

import { run, runPnpm, venvPython } from "./lib/proc.mjs";
import { paths } from "./lib/paths.mjs";

const API_BASE = process.env.API_BASE_URL ?? "http://localhost:8000";
const results = [];

function record(id, name, status, detail) {
  results.push({ id, name, status, detail });
  const mark = status === "PASS" ? "✓" : status === "SKIP" ? "–" : "✗";
  console.log(`  ${mark} [${id}] ${name} — ${detail}`);
}

async function probe(url) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const response = await fetch(url, { signal: controller.signal });
    clearTimeout(timer);
    return { ok: response.ok, status: response.status, body: await response.json() };
  } catch (error) {
    return { ok: false, error: String(error?.message ?? error) };
  }
}

console.log("\n=== M0 验收 ===\n");

/* ------------------------------------------------------ M0-1 目录结构 */
{
  const required = [
    "apps/web",
    "apps/api",
    "packages/api-types",
    "docs",
    "supabase/migrations",
    "docker-compose.yml",
    "pnpm-workspace.yaml",
  ];
  const missing = required.filter((rel) => !existsSync(join(paths.repoRoot, rel)));
  record(
    "M0-1",
    "monorepo 结构",
    missing.length === 0 ? "PASS" : "FAIL",
    missing.length === 0 ? "7 个必需路径全部存在" : `缺失：${missing.join(", ")}`,
  );
}

/* -------------------------------------------- M0-2 前端可编译（类型层） */
{
  const r = run("pnpm", ["--filter", "@resume/web", "typecheck"], {
    cwd: paths.repoRoot,
    capture: true,
    allowFailure: true,
  });
  record(
    "M0-2",
    "Next.js 工程可用",
    r.status === 0 ? "PASS" : "FAIL",
    r.status === 0 ? "tsc --noEmit 通过" : `tsc 失败：${r.stderr.trim().split("\n").slice(-3).join(" | ")}`,
  );
}

/* ---------------------------------------------------- M0-3 后端测试 */
{
  const python = venvPython(paths.apiVenv);
  if (!existsSync(python)) {
    record("M0-3", "FastAPI /health", "SKIP", "后端 venv 不存在，先执行 pnpm api:setup");
  } else {
    const r = run(python, ["-m", "pytest", "-q"], {
      cwd: paths.apiDir,
      capture: true,
      allowFailure: true,
    });
    record(
      "M0-3",
      "FastAPI /health",
      r.status === 0 ? "PASS" : "FAIL",
      r.status === 0
        ? (r.stdout.trim().split("\n").slice(-1)[0] ?? "pytest 通过")
        : "pytest 失败，见输出",
    );

    const ruff = run(python, ["-m", "ruff", "check", "."], {
      cwd: paths.apiDir,
      capture: true,
      allowFailure: true,
    });
    record("M0-3", "ruff lint", ruff.status === 0 ? "PASS" : "FAIL", ruff.status === 0 ? "0 issues" : "存在 lint 问题");
  }
}

/* ------------------------------------------------ M0-4 compose 有效性 */
{
  const r = run("docker", ["compose", "config", "--quiet"], {
    cwd: paths.repoRoot,
    capture: true,
    allowFailure: true,
  });
  if (r.status === 0) {
    const services = run("docker", ["compose", "config", "--services"], {
      cwd: paths.repoRoot,
      capture: true,
      allowFailure: true,
    });
    record("M0-4", "docker compose 编排", "PASS", `有效，服务：${services.stdout.trim().split(/\s+/).join(", ")}`);
  } else {
    record("M0-4", "docker compose 编排", "FAIL", r.stderr.trim().split("\n").slice(-2).join(" | "));
  }
}

/* --------------------------------------------------- M0-6 类型管线同步 */
{
  const r = run(process.execPath, ["scripts/gen-api-types.mjs", "--check"], {
    cwd: paths.repoRoot,
    capture: true,
    allowFailure: true,
  });
  record(
    "M0-6",
    "类型管线同步",
    r.status === 0 ? "PASS" : "FAIL",
    r.status === 0 ? "Pydantic → OpenAPI → TS 无漂移" : "类型与后端 schema 不一致",
  );
}

/* ----------------------------------- M0-5 / M0-7 需要活着的服务才能验 */
{
  const health = await probe(`${API_BASE}/api/v1/health`);
  if (health.ok) {
    record("M0-3", "运行态 /health", "PASS", `200，service=${health.body?.service}`);

    const info = await probe(`${API_BASE}/api/v1/system/info`);
    if (info.ok && info.body?.database?.connected) {
      const metaCount = info.body.meta?.length ?? 0;
      record("M0-5", "migration 已生效", metaCount > 0 ? "PASS" : "FAIL", `service_meta 读到 ${metaCount} 行`);
      record(
        "M0-7",
        "UI → API → DB 联通",
        metaCount > 0 ? "PASS" : "FAIL",
        `trace_id=${info.body.trace_id}`,
      );
    } else {
      record("M0-5", "migration 已生效", "SKIP", "数据库未连接");
      record("M0-7", "UI → API → DB 联通", "SKIP", "数据库未连接");
    }
  } else {
    record("M0-3", "运行态 /health", "SKIP", `后端未启动（${health.error ?? health.status}）`);
    record("M0-5", "migration 已生效", "SKIP", "需要后端在线");
    record("M0-7", "UI → API → DB 联通", "SKIP", "需要后端在线");
  }
}

/* --------------------------------------------------------------- 汇总 */
const pass = results.filter((r) => r.status === "PASS").length;
const fail = results.filter((r) => r.status === "FAIL").length;
const skip = results.filter((r) => r.status === "SKIP").length;

console.log(`\n=== 汇总：${pass} 通过 / ${fail} 失败 / ${skip} 跳过 ===\n`);
process.exit(fail > 0 ? 1 : 0);
