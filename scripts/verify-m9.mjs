import { paths } from "./lib/paths.mjs";
import { run, venvPython } from "./lib/proc.mjs";

run(venvPython(paths.apiVenv), ["scripts/verify_m9.py"], {
  cwd: paths.apiDir,
  env: { PYTHONUTF8: "1" },
});
