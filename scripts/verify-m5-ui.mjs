import { run, venvPython } from "./lib/proc.mjs";
import { paths } from "./lib/paths.mjs";

// Next must already be running at --web-url (default: http://127.0.0.1:3107).
// Optional browser driver: uv pip install --python <API venv Python> playwright.
// Reuses installed Chrome/Edge; the browser intercepts every /api/v1 request.
run(venvPython(paths.apiVenv), ["scripts/verify_m5_ui.py", ...process.argv.slice(2)], {
  cwd: paths.apiDir,
  env: { PYTHONUTF8: "1" },
});
