/**
 * 后端脚本入口。
 *   node scripts/run-api.mjs pytest [pytest 参数...]
 *   node scripts/run-api.mjs ruff-check
 *   node scripts/run-api.mjs ruff-format
 *   node scripts/run-api.mjs export-openapi
 *   node scripts/run-api.mjs dev
 *
 * 统一走 apps/api/.venv 里的 python，避免「机器上装了几份 Python、用了哪一份」的扯皮。
 */
import { existsSync } from "node:fs";

import { run, venvPython } from "./lib/proc.mjs";
import { paths } from "./lib/paths.mjs";

const COMMANDS = {
  pytest: (extra) => [["-m", "pytest", ...extra]],
  "ruff-check": (extra) => [["-m", "ruff", "check", ".", ...extra]],
  "ruff-format": (extra) => [["-m", "ruff", "format", ".", ...extra]],
  "export-openapi": (extra) => [["scripts/export_openapi.py", ...extra]],
  dev: (extra) => [
    [
      "-m",
      "uvicorn",
      "app.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      "8000",
      "--reload",
      ...extra,
    ],
  ],
};

const [command, ...extra] = process.argv.slice(2);

if (!command || !(command in COMMANDS)) {
  console.error(`用法：node scripts/run-api.mjs <${Object.keys(COMMANDS).join(" | ")}>`);
  process.exit(2);
}

const python = venvPython(paths.apiVenv);
if (!existsSync(python)) {
  console.error(`找不到后端虚拟环境：${python}\n先执行：pnpm api:setup`);
  process.exit(1);
}

for (const args of COMMANDS[command](extra)) {
  run(python, args, { cwd: paths.apiDir });
}
