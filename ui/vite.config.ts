import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";

// The minified bundle drops the notices of the libraries it contains, while MapLibre's
// BSD-3-Clause and Plotly's MIT licences ask that copies carry them: write every production
// dependency's licence files into third-party-licenses.txt beside the page, which the wheel and
// the Windows build ship and the footer links.
function thirdPartyLicenses(): Plugin {
  return {
    name: "earwitness-third-party-licenses",
    apply: "build",
    generateBundle() {
      const pkg = JSON.parse(readFileSync(resolve(__dirname, "package.json"), "utf8"));
      const rule = "=".repeat(78);
      const parts = [
        "Third-party software in the Earwitness front end, with the licence files each package ships.",
      ];
      for (const name of Object.keys(pkg.dependencies ?? {}).sort()) {
        const dir = resolve(__dirname, "node_modules", name);
        const meta = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
        parts.push(`\n${rule}\n${name} ${meta.version} (${meta.license ?? "see below"})\n${rule}\n`);
        for (const file of readdirSync(dir).filter((f) => /^(licen[cs]e|copying|notice)/i.test(f))) {
          parts.push(readFileSync(join(dir, file), "utf8"));
        }
      }
      this.emitFile({ type: "asset", fileName: "third-party-licenses.txt", source: parts.join("\n") });
    },
  };
}

// The build lands inside the Python package so `earwitness ui` can serve it and the wheel can
// ship it; the folder is gitignored and rebuilt by CI and the release workflow.
export default defineConfig({
  base: "./",
  plugins: [thirdPartyLicenses()],
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
