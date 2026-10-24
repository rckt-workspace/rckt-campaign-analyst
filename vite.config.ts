// Lovable already provides TanStack Start, Nitro, React and Tailwind plugins.
// Never register a duplicate nitro/vite or tanstackStart plugin here.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

// Preserve Lovable's own preview target. For Docker only, force Nitro to emit
// a standalone Node.js server + client assets in .output/.
const isNodeDeployment = process.env.RCKT_DEPLOY_TARGET === "node";

export default defineConfig({
  tanstackStart: {
    server: { entry: "server" },
  },
  ...(isNodeDeployment
    ? {
        nitro: {
          preset: "node-server",
          output: {
            dir: ".output",
            serverDir: ".output/server",
            publicDir: ".output/public",
          },
        },
      }
    : {}),
});
