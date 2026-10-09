import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  base: "/assets/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname.replace(/^\/(\w:)/, "$1"),
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/media": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: (id) =>
          id.includes("node_modules") && /recharts|d3-|victory-vendor/.test(id)
            ? "charts"
            : undefined,
      },
    },
  },
});
