import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";

export default defineConfig({
  site: "https://moosesportsempire.ca",
  integrations: [sitemap()],
  output: "static",
});
