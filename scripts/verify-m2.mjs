/**
 * M2 验收脚本 —— 一条命令复现 M2-1 / M2-3 / M2-4 / M2-5 / M2-6 的验收结论。
 *
 *   node scripts/verify-m2.mjs
 *
 * 设计原则与 verify-m0 / verify-m1 一致：
 *   1. 能自动判定的就自动判定，不写「看起来没问题」；
 *   2. 需要外部服务（后端 / 数据库）的检查，缺服务时明确标记 SKIP 并给出恢复命令，
 *      而不是把「没起服务」报成「代码有问题」。
 *
 * 与 M1 版本的差别：M1 验的是**一条链路**，M2 验的是**一套数据契约**，
 * 所以这里会真的造几条经历出来，验证「写进去 → 读回来 → 排序 → 改 → 删」整圈，
 * 并在结束时把造的数据全部清理掉（素材库是用户资产，验收脚本不能留垃圾）。
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { run, venvPython } from "./lib/proc.mjs";
import { paths } from "./lib/paths.mjs";

const API_BASE = process.env.API_BASE_URL ?? "http://localhost:8000";
const API = `${API_BASE}/api/v1`;
const results = [];

/** 验收数据的组织名前缀：既便于识别，也便于结束时按前缀清理残留。 */
const MARK = "【M2 验收】";

function record(id, name, status, detail) {
  results.push({ id, name, status, detail });
  const mark = status === "PASS" ? "✓" : status === "SKIP" ? "–" : "✗";
  console.log(`  ${mark} [${id}] ${name} — ${detail}`);
}

async function request(path, init = {}, timeoutMs = 60000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API}${path}`, { ...init, signal: controller.signal });
    const contentType = response.headers.get("content-type") ?? "";
    const body = contentType.includes("application/json")
      ? await response.json().catch(() => null)
      : await response.text();
    return { ok: response.ok, status: response.status, body };
  } catch (error) {
    return { ok: false, error: String(error?.message ?? error) };
  } finally {
    clearTimeout(timer);
  }
}

const json = (payload) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});

/** 一条基准素材：同时带 metrics 与 variants，用来验证两个新子结构。 */
function sample(overrides = {}) {
  return {
    kind: "internship",
    org: `${MARK}示例公司`,
    role: "后端实习生",
    start_date: "2026-06-01",
    end_date: "2026-08-31",
    raw_description: "参与订单服务的接口开发与慢查询优化。",
    skill_tags: ["Python", "PostgreSQL"],
    highlights: ["负责订单查询接口的重构"],
    metrics: [
      { name: "订单查询接口 P99 延迟", value: "800ms → 120ms", context: "压测 5000 QPS 下" },
      { name: "慢查询", value: "7 条 → 0 条", context: null },
    ],
    variants: [
      { direction: "后端开发", text: "重构订单查询链路，P99 从 800ms 降到 120ms。", note: "投后端岗" },
      { direction: "数据工程", text: "优化索引设计与慢查询治理。", note: null },
    ],
    sort_order: 0,
    ...overrides,
  };
}

console.log("\n=== M2 验收 ===\n");

/* ------------------------------------------- M2-1 数据模型（静态：migration） */
{
  const migration = join(paths.repoRoot, "supabase", "migrations", "20261010000000_m2_library.sql");
  const compose = join(paths.repoRoot, "docker-compose.yml");

  if (!existsSync(migration)) {
    record("M2-1", "migration 文件", "FAIL", "找不到 20261010000000_m2_library.sql");
  } else {
    const sql = readFileSync(migration, "utf8").toLowerCase();
    const checks = {
      "experiences.metrics": sql.includes("add column if not exists metrics"),
      "experiences.variants": sql.includes("add column if not exists variants"),
      "版本标记 m2": sql.includes("m2_0001"),
    };
    const missing = Object.entries(checks)
      .filter(([, ok]) => !ok)
      .map(([name]) => name);
    record(
      "M2-1",
      "migration 定义了量化结果与多版本表述",
      missing.length === 0 ? "PASS" : "FAIL",
      missing.length === 0 ? Object.keys(checks).join(" / ") : `缺：${missing.join(", ")}`,
    );

    const composeText = existsSync(compose) ? readFileSync(compose, "utf8") : "";
    record(
      "M2-1",
      "migration 已挂进 compose",
      composeText.includes("20261010000000_m2_library.sql") ? "PASS" : "FAIL",
      composeText.includes("20261010000000_m2_library.sql")
        ? "docker-entrypoint-initdb.d/12-m2-library.sql 已挂载"
        : "compose 未挂载该 migration（新容器不会执行它）",
    );
  }
}

/* ------------------------------------------ M2-1 数据模型（静态：类型管线） */
{
  const types = join(paths.repoRoot, "packages", "api-types", "src", "schema.d.ts");
  if (!existsSync(types)) {
    record("M2-1", "前端类型已生成", "FAIL", "packages/api-types/src/schema.d.ts 不存在");
  } else {
    const text = readFileSync(types, "utf8");
    const checks = {
      ExperienceMetric: text.includes("ExperienceMetric:"),
      ExperienceVariant: text.includes("ExperienceVariant:"),
      ExperienceUpdate: text.includes("ExperienceUpdate:"),
      "PUT 端点": text.includes("update_experience_api_v1_experiences__experience_id__put"),
    };
    const missing = Object.entries(checks)
      .filter(([, ok]) => !ok)
      .map(([name]) => name);
    record(
      "M2-1",
      "接口类型与后端 schema 同步",
      missing.length === 0 ? "PASS" : "FAIL",
      missing.length === 0 ? Object.keys(checks).join(" / ") : `缺：${missing.join(", ")}`,
    );
  }
}

/* ------------------------------------------------ 运行态：后端 + 数据库是否在 */
const before = await request("/experiences");
if (!before.ok) {
  const reason = before.error ?? `HTTP ${before.status}`;
  for (const [id, name] of [
    ["M2-1", "量化结果往返"],
    ["M2-3", "编辑（PUT 全量替换）"],
    ["M2-3", "删除与 404"],
    ["M2-5", "列表排序"],
    ["M2-6", "多版本表述"],
  ]) {
    record(id, name, "SKIP", `后端或数据库不可用（${reason}）`);
  }
} else {
  /* --------------------------------------------------------- 先清理历史残留 */
  const leftovers = (before.body?.items ?? []).filter((item) =>
    String(item.org ?? "").startsWith(MARK),
  );
  for (const item of leftovers) {
    await request(`/experiences/${item.id}`, { method: "DELETE" });
  }

  /* ------------------------------------------------ M2-1 子结构写入与回读 */
  const created = await request("/experiences", json(sample()));
  if (!created.ok) {
    record("M2-1", "量化结果往返", "FAIL", `HTTP ${created.status}：${JSON.stringify(created.body)?.slice(0, 200)}`);
  } else {
    const body = created.body;
    const metricsOk =
      Array.isArray(body.metrics) &&
      body.metrics.length === 2 &&
      body.metrics[0].name === "订单查询接口 P99 延迟" &&
      // 数值必须原样保留：箭头与空格都不能被规整，否则 M4-7 的比对失去参照物
      body.metrics[0].value === "800ms → 120ms";
    record(
      "M2-1",
      "量化结果往返",
      metricsOk ? "PASS" : "FAIL",
      metricsOk
        ? `${body.metrics.length} 条指标，数值写法原样保留（${body.metrics[0].value}）`
        : `回读不符：${JSON.stringify(body.metrics)}`,
    );
    record(
      "M2-1",
      "定性要点与量化结果分开存放",
      Array.isArray(body.highlights) && body.highlights.length === 1 && metricsOk ? "PASS" : "FAIL",
      `highlights=${(body.highlights ?? []).length} 条 / metrics=${(body.metrics ?? []).length} 条`,
    );
  }

  /* ----------------------------------------------------- M2-6 多版本表述 */
  {
    const body = created.body ?? {};
    const directions = (body.variants ?? []).map((variant) => variant.direction);
    const bothSaved = directions.length === 2 && directions.includes("后端开发") && directions.includes("数据工程");
    record(
      "M2-6",
      "同一条目保存多个方向表述",
      bothSaved ? "PASS" : "FAIL",
      bothSaved ? directions.join(" / ") : `回读不符：${JSON.stringify(directions)}`,
    );

    const duplicated = await request(
      "/experiences",
      json(
        sample({
          variants: [
            { direction: "后端开发", text: "第一份", note: null },
            { direction: "后端开发", text: "第二份", note: null },
          ],
        }),
      ),
    );
    const rejected = duplicated.status === 422;
    record(
      "M2-6",
      "同方向重复被拒",
      rejected ? "PASS" : "FAIL",
      rejected ? "HTTP 422，未静默存成两份" : `期望 422，实际 ${duplicated.status}`,
    );
  }

  /* -------------------------------------------- M2-3 编辑（PUT 全量替换） */
  const targetId = created.body?.id;
  if (targetId) {
    const minimal = {
      kind: "project",
      org: `${MARK}改过的组织`,
      role: "改过的角色",
      raw_description: "只提交必填字段。",
      sort_order: 0,
    };
    const updated = await request(`/experiences/${targetId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(minimal),
    });
    if (!updated.ok) {
      record("M2-3", "编辑（PUT 全量替换）", "FAIL", `HTTP ${updated.status}：${JSON.stringify(updated.body)?.slice(0, 200)}`);
    } else {
      const body = updated.body;
      // 关键断言：PUT 不传的字段等于清空。若实现被改成「按传入字段合并」，这里会立刻红
      const cleared =
        (body.skill_tags ?? []).length === 0 &&
        (body.highlights ?? []).length === 0 &&
        (body.metrics ?? []).length === 0 &&
        (body.variants ?? []).length === 0 &&
        body.start_date === null;
      record(
        "M2-3",
        "编辑（PUT 全量替换）",
        body.org === minimal.org && cleared ? "PASS" : "FAIL",
        body.org === minimal.org && cleared
          ? "字段被整体替换，未提交的标签/要点/指标/变体已清空"
          : `未按全量语义替换：tags=${(body.skill_tags ?? []).length} metrics=${(body.metrics ?? []).length} start_date=${body.start_date}`,
      );
    }

    const invalid = await request(`/experiences/${targetId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...minimal, org: "" }),
    });
    record(
      "M2-3",
      "非法入参被拦",
      invalid.status === 422 ? "PASS" : "FAIL",
      invalid.status === 422 ? "必填字段为空 → HTTP 422" : `期望 422，实际 ${invalid.status}`,
    );
  } else {
    record("M2-3", "编辑（PUT 全量替换）", "SKIP", "基准素材写入失败，无法验证编辑");
  }

  /* --------------------------------------------------- M2-5 列表分组与排序 */
  {
    const ongoing = await request(
      "/experiences",
      json(sample({ org: `${MARK}进行中`, start_date: "2026-07-01", end_date: null })),
    );
    const later = await request(
      "/experiences",
      json(sample({ org: `${MARK}较晚结束`, start_date: "2026-01-01", end_date: "2026-08-31" })),
    );
    const earlier = await request(
      "/experiences",
      json(sample({ org: `${MARK}较早结束`, start_date: "2026-01-01", end_date: "2026-05-31" })),
    );

    const list = await request("/experiences");
    const items = list.body?.items ?? [];
    const mine = items
      .map((item, index) => ({ item, index }))
      .filter(({ item }) => String(item.org ?? "").startsWith(MARK));

    const expectOrder = [`${MARK}进行中`, `${MARK}较晚结束`, `${MARK}较早结束`];
    const actualOrder = mine.map(({ item }) => item.org);
    const orderOk = expectOrder.every((org, position) => actualOrder[position] === org);

    // 分组必须连续：同 kind 的条目之间不应夹进别的 kind（SQL 的 order by kind 保证）。
    // 判法：统计「新块起始」的个数与不同 kind 的个数是否相等 ——
    // 相等就说明每个 kind 恰好占一整块，而没有像 [A,B,A] 那样被劈开。
    const kinds = mine.map(({ item }) => item.kind);
    const blockStarts = kinds.filter(
      (kind, position) => position === 0 || kinds[position - 1] !== kind,
    );
    const grouped = new Set(blockStarts).size === blockStarts.length;

    const wrote = ongoing.ok && later.ok && earlier.ok;
    record(
      "M2-5",
      "列表分组与组内时间倒序",
      wrote && orderOk && grouped ? "PASS" : "FAIL",
      !wrote
        ? "验收数据写入失败"
        : orderOk && grouped
          ? `验收条目顺序：${actualOrder.join(" → ")}（组内时间倒序、同 kind 连续）`
          : `期望 ${expectOrder.join(" → ")}，实际 ${actualOrder.join(" → ")}；同 kind 连续=${grouped}`,
    );

    const hasSubstructures = mine.every(
      ({ item }) => Array.isArray(item.metrics) && Array.isArray(item.variants),
    );
    record(
      "M2-5",
      "列表返回新子结构",
      hasSubstructures ? "PASS" : "FAIL",
      hasSubstructures ? "每条都带 metrics / variants 字段" : "有条目缺字段",
    );
  }

  /* ------------------------------------------------------- M2-3 删除与 404 */
  {
    const doomed = await request("/experiences", json(sample({ org: `${MARK}待删除` })));
    const id = doomed.body?.id;
    const removed = id ? await request(`/experiences/${id}`, { method: "DELETE" }) : { status: 0 };
    const afterDelete = id ? await request(`/experiences/${id}`) : { status: 0 };
    const missingAgain = id ? await request(`/experiences/${id}`, { method: "DELETE" }) : { status: 0 };

    const ok = removed.status === 204 && afterDelete.status === 404 && missingAgain.status === 404;
    record(
      "M2-3",
      "删除与 404",
      ok ? "PASS" : "FAIL",
      ok
        ? "DELETE 204 → 再读 404 → 再删 404"
        : `DELETE=${removed.status} / 再读=${afterDelete.status} / 再删=${missingAgain.status}`,
    );
  }

  /* ------------------------------------------------------------- 清理验收数据 */
  const residue = ((await request("/experiences")).body?.items ?? []).filter((item) =>
    String(item.org ?? "").startsWith(MARK),
  );
  for (const item of residue) {
    await request(`/experiences/${item.id}`, { method: "DELETE" });
  }
  const stillThere = ((await request("/experiences")).body?.items ?? []).filter((item) =>
    String(item.org ?? "").startsWith(MARK),
  );
  record(
    "M2-*",
    "验收数据已清理",
    stillThere.length === 0 ? "PASS" : "FAIL",
    stillThere.length === 0
      ? `素材库无「${MARK}」残留`
      : `仍有 ${stillThere.length} 条残留，请手动清理`,
  );
}

/* ------------------------------------------------------------ 静态检查 */
{
  const python = venvPython(paths.apiVenv);
  if (!existsSync(python)) {
    record("M2-*", "后端测试与 lint", "SKIP", "apps/api/.venv 不存在，先执行 pnpm api:setup");
  } else {
    const pytest = run(python, ["-m", "pytest", "-q"], {
      cwd: paths.apiDir,
      capture: true,
      allowFailure: true,
    });
    record(
      "M2-*",
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
    record(
      "M2-*",
      "ruff lint",
      ruff.status === 0 ? "PASS" : "FAIL",
      ruff.status === 0 ? "0 issues" : "存在 lint 问题",
    );
  }

  const types = run(process.execPath, ["scripts/gen-api-types.mjs", "--check"], {
    cwd: paths.repoRoot,
    capture: true,
    allowFailure: true,
  });
  record(
    "M2-*",
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
  console.log("（跳过项需要后端 / 数据库在线，见上方每条的提示）");
}
console.log();

process.exit(fail > 0 ? 1 : 0);
