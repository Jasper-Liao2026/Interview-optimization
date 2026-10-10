import { paths } from "./lib/paths.mjs";
import { run, venvPython } from "./lib/proc.mjs";

run(venvPython(paths.apiVenv), ["scripts/verify_m8.py", ...process.argv.slice(2)], { cwd: paths.apiDir });
run(venvPython(paths.apiVenv), ["scripts/verify_m8_evaluation.py"], { cwd: paths.apiDir });
