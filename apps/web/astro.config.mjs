import { defineConfig } from "astro/config";
import svelte from "@astrojs/svelte";
import tailwindcss from "@tailwindcss/vite";
import node from "@astrojs/node";

export default defineConfig({
  output: "server",
  adapter: node({ mode: "standalone" }),
  integrations: [svelte()],
  vite: {
    plugins: [tailwindcss()],
    resolve: {
      alias: {},
    },
    server: {
      allowedHosts: ["moosesportsempire.ca"],
    },
  },
  site: "https://moosesportsempire.ca",
  server: {
    port: 4321,
    host: true,
  },
  security: {
    checkOrigin: true,
  },
});
