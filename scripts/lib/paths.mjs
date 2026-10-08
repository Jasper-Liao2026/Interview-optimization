/**
 * 路径与运行时定位。所有脚本共用，保证「脚本从哪里被调用」都不影响结果。
 */
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

export const paths = {
  repoRoot: REPO_ROOT,
  apiDir: join(REPO_ROOT, "apps", "api"),
  apiVenv: join(REPO_ROOT, "apps", "api", ".venv"),
  openapiJson: join(REPO_ROOT, "apps", "api", "openapi", "openapi.json"),
  typesPackage: join(REPO_ROOT, "packages", "api-types"),
  typesFile: join(REPO_ROOT, "packages", "api-types", "src", "schema.d.ts"),
  /** 项目自带的工具链 venv：只装 uv，避免污染用户全局环境 */
  toolsVenv: join(REPO_ROOT, "scripts", ".tools", "uv"),
};

/** 找一个能用来创建 venv 的基础 Python */
export function findBasePython() {
  const candidates = [
    process.env.RESUME_PYTHON,
    // WorkBuddy 托管 Python（本机默认可用）
    "C:\\Users\\liaoh\\.workbuddy\\binaries\\python\\versions\\3.13.12\\python.exe",
    "python",
    "python3",
    "py",
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (candidate.includes("\\") || candidate.includes("/")) {
      if (existsSync(candidate)) return candidate;
      continue;
    }
    return candidate; // 交给 spawn 去解析 PATH
  }
  return "python";
}
