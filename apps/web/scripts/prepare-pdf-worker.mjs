import { copyFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
copyFileSync(
  require.resolve("pdfjs-dist/build/pdf.worker.min.mjs"),
  fileURLToPath(new URL("../public/pdf.worker.min.mjs", import.meta.url)),
);
