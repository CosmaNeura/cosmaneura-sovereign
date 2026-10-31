// Reproducible delivery bytes; app/panel.js remains the readable review source.
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const [runtimeWeb, mode] = process.argv.slice(2);
if (!runtimeWeb || !["write", "check"].includes(mode)) throw new Error("Use <exact locked Core web runtime> write|check");
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
const lockSha = "ae312f69d18c0a80b06e28c9cf5f0fa432c483171e6108c719ed958bfbde5c31";
const packageSha = "b33803c043507c2a4f331a176667ec84c58c9c90fdf229999e5adb4aa1433090";
if (sha(readFileSync(resolve(runtimeWeb, "pnpm-lock.yaml"))) !== lockSha ||
    sha(readFileSync(resolve(runtimeWeb, "package.json"))) !== packageSha) throw new Error("Compiler runtime lock mismatch");
const compiler = createRequire(import.meta.url)(resolve(runtimeWeb, "node_modules/.pnpm/esbuild@0.25.12/node_modules/esbuild"));
if (compiler.version !== "0.25.12") throw new Error("Compiler version mismatch");
const source = readFileSync(resolve(root, "app/panel.js"));
const result = await compiler.transform(source.toString("utf8"), {
  loader: "js", minify: true, target: "es2020", charset: "utf8", legalComments: "none", sourcemap: false,
});
const bytes = Buffer.from(result.code, "utf8");
const path = resolve(root, "app/panel.delivery.js"), manifestPath = resolve(root, "app/panel.delivery.json");
const git = (...args) => execFileSync("git", args, { cwd: root, encoding: "utf8" }).trim();
if (mode === "write") {
  if (git("status", "--porcelain", "--", "app/panel.js")) throw new Error("Commit readable source before generating delivery");
  writeFileSync(path, bytes);
  writeFileSync(manifestPath, JSON.stringify({
    schema_version: "1.0.0", source_commit: git("rev-parse", "HEAD"),
    source_path: "app/panel.js", source_blob: git("rev-parse", "HEAD:app/panel.js"), source_sha256: sha(source),
    delivery_path: "app/panel.delivery.js", delivery_sha256: sha(bytes), compiler: "esbuild", compiler_version: compiler.version,
    compiler_lock_sha256: lockSha, compiler_package_sha256: packageSha,
    options: { loader: "js", minify: true, target: "es2020", charset: "utf8", legalComments: "none", sourcemap: false },
  }, null, 2) + "\n");
} else {
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  if (!bytes.equals(readFileSync(path)) || manifest.delivery_sha256 !== sha(bytes) || manifest.source_sha256 !== sha(source) ||
      manifest.compiler_lock_sha256 !== lockSha || manifest.compiler_package_sha256 !== packageSha || manifest.compiler_version !== compiler.version ||
      !execFileSync("git", ["show", `${manifest.source_commit}:app/panel.js`], { cwd: root }).equals(source) ||
      git("rev-parse", `${manifest.source_commit}:app/panel.js`) !== manifest.source_blob) throw new Error("Delivery provenance mismatch");
}
process.stdout.write(JSON.stringify({ source_sha256: sha(source), delivery_sha256: sha(bytes), bytes: bytes.length }) + "\n");
