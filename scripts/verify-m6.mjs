import { run, venvPython } from "./lib/proc.mjs";
import { paths } from "./lib/paths.mjs";

run(venvPython(paths.apiVenv), ["scripts/verify_m6.py"], {
  cwd: paths.apiDir,
  env: { PYTHONUTF8: "1" },
});
