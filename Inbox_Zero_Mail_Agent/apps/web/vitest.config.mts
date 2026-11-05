import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { config } from "dotenv";
import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

const require = createRequire(import.meta.url);
const zodV4CorePath = require.resolve("zod/v4/core");
// apps/web and apps/worker can resolve different physical copies of these
// packages (pnpm peer graphs). Alias them so vi.mock("bullmq"|"ioredis")
// applies to both the web dispatch path and the worker runtime.
const bullmqPath = require.resolve("bullmq");
const ioredisPath = require.resolve("ioredis");
const envFile = "./.env.test";
const env = existsSync(envFile) ? config({ path: envFile }).parsed : undefined;

export default defineConfig({
  plugins: [tsconfigPaths()],
  // Vitest runs outside Next, so it must compile JSX instead of inheriting
  // Next's tsconfig `jsx: "preserve"` setting.
  esbuild: {
    jsx: "automatic",
  },
  resolve: {
    alias: {
      "zod/v4/core": zodV4CorePath,
      bullmq: bullmqPath,
      ioredis: ioredisPath,
    },
  },
  test: {
    environment: "node",
    setupFiles: ["./__tests__/setup.ts"],
    maxWorkers: 1,
    fileParallelism: false,
    server: {
      deps: {
        inline: [/@hookform\/resolvers/],
      },
    },
    env: {
      ...env,
    },
  },
});
