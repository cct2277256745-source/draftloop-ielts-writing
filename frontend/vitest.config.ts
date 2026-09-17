import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["tests/**", "dist/**", "node_modules/**"],
    setupFiles: ["./src/test/setup.ts"],
    css: true,
  },
});
