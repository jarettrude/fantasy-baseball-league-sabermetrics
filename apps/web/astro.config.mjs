import node from "@astrojs/node";
import svelte from "@astrojs/svelte";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "astro/config";

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
			allowedHosts: ["example.com"],
		},
	},
	site: "https://example.com",
	server: {
		port: 4321,
		host: true,
	},
	security: {
		checkOrigin: true,
	},
});
