/**
 * Shared Prettier configuration for the web app.
 * Enables Astro + Svelte formatting and Tailwind class sorting.
 */
export default {
  plugins: [
    "prettier-plugin-tailwindcss",
    "prettier-plugin-astro",
    "prettier-plugin-svelte",
  ],
  tailwindFunctions: ["clsx", "cn"],
};
