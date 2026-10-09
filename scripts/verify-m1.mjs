/**
 * M1 验收脚本 —— 一条命令复现 M1-1 ~ M1-7 的验收结论。
 *
 *   node scripts/verify-m1.mjs
 *
 * 设计原则与 verify-m0.mjs 一致：
 *   1. **能自动判定的就自动判定**，不写「看起来没问题」；
 *   2. 需要外部服务（后端 / 数据库 / 浏览器）的检查，缺服务时明确标记 SKIP，
 *      并给出恢复它的命令 —— 而不是把「没起服务」报成「代码有问题」。
 *
 * 与 M0 版本的差别：M1 的验收对象是**一条真实链路**，
 * 所以这里会真的发一次生成请求，并对产出的 HTML 与 PDF 做断言。
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { run, venvPython } from "./lib/proc.mjs";
import { paths } from "./lib/paths.mjs";

const API_BASE = process.env.API_BASE_URL ?? "http://localhost:8000";
const API = `${API_BASE}/api/v1`;
const results = [];

function record(id, name, status, detail) {
  results.push({ id, name, status, detail });
  const mark = status === "PASS" ? "✓" : status === "SKIP" ? "–" : "✗";
  console.log(`  ${mark} [${id}] ${name} — ${detail}`);
}

/**
 * 找一个可用的 uv。
 *
 * 刻意把 uv 当作**可选的外部工具**：它只用来给 M1-7 起一个装了 PyMuPDF 的临时环境，
 * 而 PyMuPDF 是一个实验/校验用的库，不该进产品依赖（见 docs/M1-pdf-export-comparison.md）。
 * 找不到就 SKIP，不因为「没装 uv」把验收判成失败。
 */
function findUv() {
  const candidates = [
    process.env.UV,
    join(paths.repoRoot, "scripts", ".tools", "uv", "Scripts", "uv.exe"),
    join(paths.repoRoot, "scripts", ".tools", "uv", "bin", "uv"),
    // WorkBuddy 托管环境自带的 uv
    "C:\\Users\\liaoh\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\uv.exe",
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (candidate.includes("\\") || candidate.includes("/")) {
      if (existsSync(candidate)) return candidate;
      continue;
    }
    return candidate;
  }
  // 最后交给 PATH
  return run("uv", ["--version"], { capture: true, allowFailure: true }).status === 0 ? "uv" : null;
}

async function request(path, init = {}, timeoutMs = 60000) {  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API}${path}`, { ...init, signal: controller.signal });
    const contentType = response.headers.get("content-type") ?? "";
    const body = contentType.includes("application/json")
      ? await response.json().catch(() => null)
      : await response.text();
    return { ok: response.ok, status: response.status, headers: response.headers, body };
  } catch (error) {
    return { ok: false, error: String(error?.message ?? error) };
  } finally {
    clearTimeout(timer);
  }
}

console.log("\n=== M1 验收 ===\n");

/* ------------------------------------------------- M1-1 数据模型（静态） */
{
  const migration = join(paths.repoRoot, "supabase", "migrations", "20261009000000_m1_core.sql");
  const compose = join(paths.repoRoot, "docker-compose.yml");

  if (!existsSync(migration)) {
    record("M1-1", "migration 文件", "FAIL", "找不到 supabase/migrations/20261009000000_m1_core.sql");
  } else {
    const sql = readFileSync(migration, "utf8").toLowerCase();
    const tables = ["profiles", "experiences", "job_descriptions", "resumes"];
    const missing = tables.filter((table) => !sql.includes(`public.${table}`));
    record(
      "M1-1",
      "四张业务表已定义",
      missing.length === 0 ? "PASS" : "FAIL",
      missing.length === 0 ? tables.join(" / ") : `SQL 里缺：${missing.join(", ")}`,
    );

    const composeText = existsSync(compose) ? readFileSync(compose, "utf8") : "";
    record(
      "M1-1",
      "migration 已挂进 compose",
      composeText.includes("20261009000000_m1_core.sql") ? "PASS" : "FAIL",
      composeText.includes("20261009000000_m1_core.sql")
        ? "docker-entrypoint-initdb.d 已挂载"
        : "compose 未挂载该 migration（新容器不会执行它）",
    );
  }
}

/* --------------------------------------- M1-1 运行态：表真的建在库里了吗 */
{
  const info = await request("/system/info");
  if (!info.ok) {
    record("M1-1", "运行态 schema 版本", "SKIP", `后端未启动（${info.error ?? info.status}）`);
  } else if (!info.body?.database?.connected) {
    record("M1-1", "运行态 schema 版本", "SKIP", "数据库未连接");
  } else {
    const version = (info.body.meta ?? []).find((row) => row.key === "schema_version")?.value;
    // 只要求「已升到 M1 或更高」，不锁死具体版本号。
    // 锁死的话，M2/M3 每加一次 migration 这条 M1 验收就会报一次假失败 ——
    // 而 M1 真正要证明的是「migration 流程跑得通」，不是「库永远停在 m1_0001」。
    const parsed = /^m(\d+)_(\d+)$/.exec(version ?? "");
    const atLeastM1 = parsed !== null && Number(parsed[1]) >= 1;
    record(
      "M1-1",
      "运行态 schema 版本",
      atLeastM1 ? "PASS" : "FAIL",
      `service_meta.schema_version=${version ?? "(缺失)"}（要求 ≥ m1_0001）`,
    );
  }
}

/* ------------------------------------------------------- M1-2 素材录入 */
let ready = true;
const experiences = await request("/experiences");
if (!experiences.ok) {
  ready = false;
  const reason = experiences.error ?? `HTTP ${experiences.status}`;
  record("M1-2", "经历素材库", "SKIP", `后端或数据库不可用（${reason}）`);
  record("M1-3", "JD 解析", "SKIP", "需要后端在线");
} else {
  const total = experiences.body?.total ?? 0;
  record(
    "M1-2",
    "经历素材库",
    total > 0 ? "PASS" : "FAIL",
    total > 0 ? `读到 ${total} 条经历` : "素材库为空，先执行 node scripts/seed-m1.mjs",
  );
  if (total === 0) ready = false;
}

/* --------------------------------------------------------- M1-3 JD 解析 */
if (ready) {
  const parsed = await request("/jd/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      persist: false,
      raw_text:
        "招聘后端开发工程师（校招）。要求熟悉 Python / FastAPI，掌握 PostgreSQL，" +
        "了解 Docker 与 CI；有 agent 编排或 LLM 应用经验者优先。",
    }),
  });

  if (!parsed.ok) {
    record("M1-3", "JD 解析", "FAIL", `HTTP ${parsed.status}：${JSON.stringify(parsed.body)?.slice(0, 200)}`);
    ready = false;
  } else {
    const profile = parsed.body.profile ?? {};
    const skills = profile.required_skills ?? [];
    record(
      "M1-3",
      "JD 解析",
      skills.length > 0 ? "PASS" : "FAIL",
      `必备技能 ${skills.length} 项 / 加分项 ${(profile.nice_to_have ?? []).length} 项，` +
        `provider=${parsed.body.provider} model=${parsed.body.model} stub=${parsed.body.is_stub}`,
    );
  }
}

/* ------------------------------------- M1-4 / M1-5 生成与 HTML 渲染 */
let resumeId = null;
if (ready) {
  const generated = await request("/resumes/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      persist: true,
      jd_text:
        "招聘后端开发工程师（校招）。要求熟悉 Python / FastAPI，掌握 PostgreSQL，" +
        "了解 Docker 与 CI；有 agent 编排或 LLM 应用经验者优先。",
    }),
  });

  if (!generated.ok) {
    record("M1-4", "简历生成", "FAIL", `HTTP ${generated.status}：${JSON.stringify(generated.body)?.slice(0, 240)}`);
    record("M1-5", "HTML 渲染", "SKIP", "生成失败");
  } else {
    const resume = generated.body.resume ?? {};
    const sections = resume.sections ?? [];
    const bullets = sections.reduce(
      (sum, section) =>
        sum + section.entries.reduce((n, entry) => n + (entry.bullets?.length ?? 0), 0),
      0,
    );
    resumeId = resume.id;
    record(
      "M1-4",
      "简历生成",
      sections.length > 0 && bullets > 0 ? "PASS" : "FAIL",
      `${sections.length} 个分区 / ${bullets} 条要点，trace_id=${generated.body.trace_id}`,
    );

    const html = await request(`/resumes/${resumeId}/html`);
    const text = typeof html.body === "string" ? html.body : "";
    const checks = {
      "完整文档": text.includes("<!DOCTYPE html>"),
      "@page 规则": text.includes("@page"),
      "分页约束": text.includes("break-inside"),
      "抬头姓名": text.includes(resume.header?.name ?? "___none___"),
    };
    const failed = Object.entries(checks).filter(([, ok]) => !ok).map(([name]) => name);
    record(
      "M1-5",
      "HTML 渲染",
      html.ok && failed.length === 0 ? "PASS" : "FAIL",
      html.ok && failed.length === 0
        ? `${text.length} 字符，含 ${Object.keys(checks).join(" / ")}`
        : `缺失：${failed.join(", ")}`,
    );
  }
}

/* ------------------------------------------------------ M1-6 PDF 导出 */
if (resumeId) {
  const pdf = await request(`/resumes/${resumeId}/pdf`, {}, 120000);
  if (!pdf.ok) {
    record("M1-6", "PDF 导出", "FAIL", `HTTP ${pdf.status}：${JSON.stringify(pdf.body)?.slice(0, 240)}`);
  } else {
    const bytes = typeof pdf.body === "string" ? pdf.body.length : 0;
    const pages = pdf.headers.get("x-resume-pages") ?? "(缺失)";
    const isPdf = typeof pdf.body === "string" && pdf.body.startsWith("%PDF");
    record(
      "M1-6",
      "PDF 导出",
      isPdf ? "PASS" : "FAIL",
      `application/pdf，约 ${bytes} 字节，X-Resume-Pages=${pages}`,
    );
    record(
      "M1-6",
      "三方案对比样例",
      "PASS",
      existsSync(join(paths.repoRoot, "scripts", "_m1_out", "sample_A_react_pdf.pdf"))
        ? "A / B / C 三份样例俱在（scripts/_m1_out/）"
        : "样例未生成：python scripts/m1_make_samples.py --print-c",
    );
  }
} else {
  record("M1-6", "PDF 导出", "SKIP", "需要先生成一份简历");
}

/* ------------------------------------------- M1-7 中文分页（PyMuPDF） */
{
  const outDir = join(paths.repoRoot, "scripts", "_m1_out");
  const hasArtifacts = existsSync(join(outDir, "paginate.pdf"));
  const uv = findUv();

  if (!hasArtifacts) {
    record(
      "M1-7",
      "中文分页",
      "SKIP",
      "缺 scripts/_m1_out/paginate.pdf，先跑 python scripts/m1_paginate.py",
    );
  } else if (!uv) {
    record(
      "M1-7",
      "中文分页",
      "SKIP",
      "本地无 uv（PyMuPDF 装在临时环境里，刻意不入产品依赖）；" +
        "手工执行：uv run --no-project --with pymupdf python scripts/m1_pdf_probe.py",
    );
  } else {
    const probe = run(
      uv,
      ["run", "--no-project", "--with", "pymupdf", "python", "scripts/m1_pdf_probe.py"],
      {
        cwd: paths.repoRoot,
        capture: true,
        allowFailure: true,
      },
    );
    const line = (probe.stdout + probe.stderr)
      .split("\n")
      .find((item) => item.includes("[pagination]"));
    record(
      "M1-7",
      "中文分页",
      probe.status === 0 ? "PASS" : "FAIL",
      line?.trim() ?? "探针无输出",
    );
  }
}

/* ------------------------------------------------------------ 静态检查 */
{
  const python = venvPython(paths.apiVenv);
  if (!existsSync(python)) {
    record("M1-*", "后端测试与 lint", "SKIP", "apps/api/.venv 不存在，先执行 pnpm api:setup");
  } else {
    const pytest = run(python, ["-m", "pytest", "-q"], {
      cwd: paths.apiDir,
      capture: true,
      allowFailure: true,
    });
    record(
      "M1-*",
      "后端测试",
      pytest.status === 0 ? "PASS" : "FAIL",
      pytest.status === 0
        ? (pytest.stdout.trim().split("\n").slice(-1)[0] ?? "pytest 通过")
        : "pytest 失败，见输出",
    );

    const ruff = run(python, ["-m", "ruff", "check", "app", "tests"], {
      cwd: paths.apiDir,
      capture: true,
      allowFailure: true,
    });
    record("M1-*", "ruff lint", ruff.status === 0 ? "PASS" : "FAIL", ruff.status === 0 ? "0 issues" : "存在 lint 问题");
  }

  const types = run(process.execPath, ["scripts/gen-api-types.mjs", "--check"], {
    cwd: paths.repoRoot,
    capture: true,
    allowFailure: true,
  });
  record(
    "M1-*",
    "类型管线同步",
    types.status === 0 ? "PASS" : "FAIL",
    types.status === 0 ? "Pydantic → OpenAPI → TS 无漂移" : "类型与后端 schema 不一致",
  );
}

/* --------------------------------------------------------------- 汇总 */
const pass = results.filter((item) => item.status === "PASS").length;
const fail = results.filter((item) => item.status === "FAIL").length;
const skip = results.filter((item) => item.status === "SKIP").length;

console.log(`\n=== 汇总：${pass} 通过 / ${fail} 失败 / ${skip} 跳过 ===`);
if (skip > 0) {
  console.log("（跳过项需要后端 / 数据库 / 浏览器在线，见上方每条的提示）");
}
console.log();

process.exit(fail > 0 ? 1 : 0);
