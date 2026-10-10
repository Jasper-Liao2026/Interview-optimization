import { paths } from "./lib/paths.mjs";
import { run, venvPython } from "./lib/proc.mjs";

run(venvPython(paths.apiVenv), ["scripts/eval_m8.py", ...process.argv.slice(2)], { cwd: paths.apiDir });
