import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

const DATA_PREFIXES = ["/catalog", "/orbital", "/rover"];

export default defineConfig(({ mode }) => {
  const origin = loadEnv(mode, ".", "").MSV_DATA_ORIGIN;
  const proxy = origin
    ? Object.fromEntries(DATA_PREFIXES.map((p) => [p, { target: origin, changeOrigin: true }]))
    : undefined;
  return {
    base: "/viewer/",
    build: { target: "es2022", chunkSizeWarningLimit: 2000 },
    server: { proxy },
    test: { environment: "node", include: ["tests/**/*.test.ts"] },
  };
});
