import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const template = readFileSync(join(root, ".env.example"), "utf8");
const composeTemplate = readFileSync(join(root, "docker-compose.yml"), "utf8");
assert(composeTemplate.includes("20261016000000_m8_prompt_bundle.sql:/docker-entrypoint-initdb.d/18-m8-prompt-bundle.sql"),
  "Compose must mount the complete M8 prompt bundle migration");
const documented = new Set([...template.matchAll(/^\s*#?\s*([A-Z][A-Z0-9_]*)=/gm)].map(match => match[1]));
const fields = [...readFileSync(join(root, "apps/api/app/config.py"), "utf8")
  .matchAll(/^    ([a-z][a-z0-9_]*):/gm)].map(match => match[1].toUpperCase());
for (const field of fields) assert(documented.has(field), `missing template variable ${field}`);

const temp = mkdtempSync(join(tmpdir(), "resume-config-"));
try {
  // Test the exact supplied template without writing/overwriting a user's .env.
  // env_file passes all active values; Compose interpolation handles ports/build.
  const compose = readFileSync(join(root, "docker-compose.yml"), "utf8")
    .replace("path: .env", `path: ${JSON.stringify(join(root, ".env.example"))}`);
  const configPath = join(temp, "compose.yml");
  writeFileSync(configPath, compose);
  const cleanEnv = { ...process.env };
  for (const name of documented) delete cleanEnv[name];
  const result = spawnSync("docker", ["compose", "--project-directory", root, "--env-file",
    join(root, ".env.example"), "-f", configPath, "config", "--format", "json"],
  { env: cleanEnv, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  const services = JSON.parse(result.stdout).services;
  const api = services.api;
  for (const prefix of ["LLM", "JUDGE", "EMBEDDING"]) {
    assert.equal(api.environment[`${prefix}_PROVIDER`], "stub");
    assert(!api.environment[`${prefix}_API_KEY`], `${prefix} has a non-demo key`);
  }
  assert.equal(new URL(api.environment.DATABASE_URL).hostname, "postgres");
  for (const match of template.matchAll(/^([A-Z][A-Z0-9_]*)=(.*)$/gm)) {
    const [, name, value] = match;
    if (name.startsWith("NEXT_PUBLIC_") || name.endsWith("_PORT")) continue;
    assert.equal(api.environment[name], value.trim(), `env_file does not pass ${name}`);
  }
  assert.equal(services.web.build.args.NEXT_PUBLIC_API_BASE_URL, "http://127.0.0.1:8000");
  assert(!api.command?.includes("--reload"), "default API must run built code");
  assert(!api.volumes?.some(volume => volume.target === "/app/app"), "default API shadows image");
  for (const name of ["web", "api", "postgres"]) {
    assert(services[name].ports.every(port => port.host_ip === "127.0.0.1"), `${name} not local`);
  }
  console.log(`M9 config: PASS (${fields.length} API variables documented; no-key Compose config valid)`);
} finally {
  assert.equal(dirname(resolve(temp)), resolve(tmpdir()));
  assert(basename(temp).startsWith("resume-config-"));
  rmSync(temp, { recursive: true, force: true });
}
