import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built straight into the directory FastAPI serves, so there is one server in
// production and no separate static host. `npm run dev` proxies the API to
// uvicorn so the same code runs in both modes without a build-time switch.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: {
    outDir: "../jana/static/app",
    emptyOutDir: true,
  },
  server: {
    port: 5180,
    proxy: { "/api": "http://127.0.0.1:8420" },
  },
});
