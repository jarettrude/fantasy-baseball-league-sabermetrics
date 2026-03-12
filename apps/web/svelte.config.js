/**
 * Svelte configuration for Astro integration.
 *
 * Configures Vite preprocessing for Svelte components within
 * the Astro build system, enabling modern Svelte features
 * and optimal build performance.
 */

import { vitePreprocess } from "@astrojs/svelte";

export default {
  preprocess: [vitePreprocess()],
};
