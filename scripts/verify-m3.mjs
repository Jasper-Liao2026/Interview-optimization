/** M3: actual API/Postgres acceptance, or opt-in real model quality evaluation. */
import { run, venvPython } from "./lib/proc.mjs";
import { paths } from "./lib/paths.mjs";

const evaluate = process.argv.includes("--eval");
run(venvPython(paths.apiVenv), [
  evaluate ? "scripts/eval_m3.py" : "scripts/verify_m3.py",
  ...process.argv.slice(2).filter((arg) => arg !== "--eval"),
], { cwd: paths.apiDir });
