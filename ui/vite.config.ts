import { defineConfig } from "vite";

// The build lands inside the Python package so `earwitness ui` can serve it and the wheel can
// ship it; the folder is gitignored and rebuilt by CI and the release workflow.
export default defineConfig({
  base: "./",
  build: {
    outDir: "../src/earwitness/server/static",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 6000,
  },
  server: {
    port: 5173,
    proxy: {
      // `earwitness ui --serve --port 8765` for live front-end development
      "/api": { target: process.env.EARWITNESS_API ?? "http://127.0.0.1:8765", changeOrigin: false },
    },
  },
});
