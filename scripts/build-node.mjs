// Builds the standalone Node.js server (.output/) used by Docker and the end-to-end tests.
// Setting the variable here keeps the command identical on Windows and POSIX shells.
import { spawnSync } from "node:child_process";

const result = spawnSync("bun", ["run", "build"], {
  stdio: "inherit",
  shell: true,
  env: { ...process.env, RCKT_DEPLOY_TARGET: "node" },
});

process.exit(result.status ?? 1);
