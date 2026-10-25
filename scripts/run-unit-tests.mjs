// Runs the pure unit tests in src/lib (*.test.ts) with node:test.
// esbuild, already a dependency of Vite, compiles each test and the module it imports into a temporary folder.
// Nothing is added to the repository and no test framework is required.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceDir = path.join(root, "src", "lib");
const outDir = mkdtempSync(path.join(tmpdir(), "rckt-unit-"));

try {
  const tests = readdirSync(sourceDir).filter((file) => file.endsWith(".test.ts"));
  if (tests.length === 0) throw new Error("no unit tests found in src/lib");

  const outputs = [];
  for (const file of tests) {
    const outfile = path.join(outDir, file.replace(/\.ts$/, ".mjs"));
    await build({
      entryPoints: [path.join(sourceDir, file)],
      bundle: true,
      platform: "node",
      format: "esm",
      outfile,
      logLevel: "warning",
    });
    outputs.push(outfile);
  }

  execFileSync(process.execPath, ["--test", ...outputs], { stdio: "inherit" });
} finally {
  rmSync(outDir, { recursive: true, force: true });
}
