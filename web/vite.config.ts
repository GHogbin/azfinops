import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev: the UI runs on 127.0.0.1:5173 and proxies /api to the local API (azfinops-serve).
export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
  test: { environment: "node" },
});
