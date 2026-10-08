/**
 * 后端环境准备（幂等，可反复执行）：
 *   1. 在 scripts/.tools/uv 建一个只装 uv 的工具 venv —— 不碰用户的全局环境
 *   2. 用这个 uv 在 apps/api 下 `uv sync`，按 pyproject.toml + uv.lock 装依赖
 *
 * 为什么不用「先全局装 uv」：全局污染、版本漂移、CI 里还得再装一遍。
 * 这里把 uv 也当成项目依赖来管。
 */
import { existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

import { exe, has, run, venvPython } from "./lib/proc.mjs";
import { findBasePython, paths } from "./lib/paths.mjs";

function ensureUv() {
  const uvPath = venvPython(paths.toolsVenv).replace(/python(\.exe)?$/, exe("uv"));

  if (existsSync(uvPath)) {
    console.log(`[setup] uv 已就绪：${uvPath}`);
    return uvPath;
  }

  const basePython = findBasePython();
  console.log(`[setup] 未发现项目内 uv，用 ${basePython} 创建工具 venv`);
  mkdirSync(dirname(paths.toolsVenv), { recursive: true });

  run(basePython, ["-m", "venv", paths.toolsVenv]);
  run(venvPython(paths.toolsVenv), ["-m", "pip", "install", "--quiet", "--upgrade", "pip"]);
  run(venvPython(paths.toolsVenv), ["-m", "pip", "install", "--quiet", "uv"]);

  if (!existsSync(uvPath)) {
    throw new Error(`uv 安装后仍未找到可执行文件：${uvPath}`);
  }
  console.log(`[setup] uv 安装完成：${uvPath}`);
  return uvPath;
}

const uv = ensureUv();

console.log("[setup] uv sync（apps/api）…");
run(uv, ["sync", "--project", paths.apiDir], { cwd: paths.repoRoot });

const python = venvPython(paths.apiVenv);
if (!existsSync(python)) {
  throw new Error(`uv sync 之后仍未生成 venv：${python}`);
}

const version = run(python, ["--version"], { capture: true }).stdout.trim();
console.log(`[setup] 完成。后端解释器：${python} (${version})`);

if (!has(uv, ["--version"])) {
  process.exitCode = 1;
}
