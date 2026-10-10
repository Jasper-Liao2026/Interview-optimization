import { run, venvPython } from "./lib/proc.mjs";
import { paths } from "./lib/paths.mjs";

const evaluate = process.argv.includes("--eval");
run(venvPython(paths.apiVenv), [
  evaluate ? "scripts/eval_m5.py" : "scripts/verify_m5.py",
  ...process.argv.slice(2).filter((arg) => arg !== "--eval"),
], { cwd: paths.apiDir, env: { PYTHONUTF8: "1" } });
