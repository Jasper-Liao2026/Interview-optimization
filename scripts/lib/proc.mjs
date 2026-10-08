/**
 * 跨平台子进程工具。
 *
 * 为什么不用 shell 拼接命令：本机是 Windows，`&&` 在 PowerShell 5.1 下不可用，
 * 用 spawnSync + 参数数组能绕开 shell 差异与中文路径的编码问题。
 */
import { spawnSync } from "node:child_process";

export const isWindows = process.platform === "win32";

/** 可执行文件后缀（Windows 下有些工具必须带 .exe 才能被 spawn 找到） */
export function exe(name) {
  return isWindows ? `${name}.exe` : name;
}

/** venv 内的目录名 */
export const venvBinDir = isWindows ? "Scripts" : "bin";

export function venvPython(venvRoot) {
  return isWindows
    ? `${venvRoot}\\Scripts\\python.exe`
    : `${venvRoot}/bin/python`;
}

/**
 * 运行命令。默认继承 stdio（让输出实时可见）；capture=true 时收集输出。
 *
 * `shell` 默认 false（避免 shell 差异与中文路径的编码问题）；
 * 但 Windows 上的 `.cmd`/`.bat` 包装脚本（npm.cmd、pnpm.cmd）在 Node 22 下
 * 必须开 shell 才能被 spawn，调用方按需传入 shell: true。
 * @returns {{status:number, stdout:string, stderr:string}}
 */
export function run(cmd, args, { cwd, capture = false, allowFailure = false, env, shell = false } = {}) {
  const result = spawnSync(cmd, args, {
    cwd,
    env: env ? { ...process.env, ...env } : process.env,
    stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit",
    encoding: "utf8",
    shell,
  });

  if (result.error) {
    if (allowFailure) {
      return { status: -1, stdout: "", stderr: String(result.error.message ?? result.error) };
    }
    throw new Error(`无法执行 ${cmd}：${result.error.message}`);
  }

  const status = result.status ?? -1;
  if (status !== 0 && !allowFailure) {
    const tail = (result.stderr || "").trim().split("\n").slice(-8).join("\n");
    throw new Error(`命令失败（exit ${status}）：${cmd} ${args.join(" ")}\n${tail}`);
  }

  return { status, stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
}

/** 命令是否存在 */
export function has(cmd, args = ["--version"]) {
  return run(cmd, args, { capture: true, allowFailure: true }).status === 0;
}

/**
 * 运行 pnpm。
 * Windows 上 pnpm 是 .ps1/.cmd 包装脚本，spawn 不经过 shell 会 ENOENT，
 * 因此这里必须开 shell（参数里都是无空格的简单值，不会被重新切分）。
 */
export function runPnpm(args, opts = {}) {
  return run(isWindows ? "pnpm.cmd" : "pnpm", args, { ...opts, shell: isWindows });
}

/**
 * 运行 npm。用途只有一个：本机 pnpm 不可用时的降级路径
 * （pnpm 在 Windows 上需要开发者模式才能建符号链接，见 docs/M0-summary.md §7.1）。
 */
export function runNpm(args, opts = {}) {
  return run(isWindows ? "npm.cmd" : "npm", args, { ...opts, shell: isWindows });
}

/** 优先 pnpm，缺失时退回 npm。返回实际使用的包管理器名。 */
export function runPreferredPkgManager(args, opts = {}) {
  if (has(isWindows ? "pnpm.cmd" : "pnpm")) {
    return { pm: "pnpm", result: runPnpm(args, opts) };
  }
  return { pm: "npm", result: runNpm(args, opts) };
}
