<!--
  Footer - Site footer with theme toggle

  Bold brutalist footer with light/dark theme toggle, privacy link, and branding.
-->
<script lang="ts">
  import { onMount } from "svelte";

  let isDark = $state(true);

  onMount(() => {
    const saved = localStorage.getItem("mse-theme");
    if (saved === "light") {
      isDark = false;
    } else if (saved === "dark") {
      isDark = true;
    } else {
      isDark = true;
    }
  });

  $effect(() => {
    document.documentElement.setAttribute(
      "data-theme",
      isDark ? "dark" : "light",
    );
    localStorage.setItem("mse-theme", isDark ? "dark" : "light");
  });

  function toggleTheme() {
    isDark = !isDark;
  }
</script>

<footer class="mt-auto border-t border-(--color-border) stitch-border-top">
  <!-- Top bar: branding + toggle -->
  <div class="bg-(--color-surface) py-4">
    <div
      class="mx-auto max-w-7xl px-page flex flex-col sm:flex-row items-center justify-between gap-3"
    >
      <div class="flex flex-col items-center sm:items-start gap-0.5">
        <span
          class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
        >
          // MSE SABERMETRIC PLATFORM
        </span>
        <span
          class="font-display text-sm font-bold text-(--color-text-secondary)"
        >
          Moose Sports Empire
        </span>
      </div>

      <!-- Theme toggle -->
      <button
        onclick={toggleTheme}
        aria-label="Toggle {isDark ? 'light' : 'dark'} theme"
        class="flex items-center gap-2 font-mono text-xs text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
      >
        <span
          class="relative inline-block h-4 w-8 rounded-full bg-(--color-surface-raised) border border-(--color-border)"
        >
          <span
            class="absolute top-0.5 h-3 w-3 rounded-full border border-(--color-border-heavy) transition-all duration-200 {isDark
              ? 'left-0.5 bg-(--color-commissioner)'
              : 'left-[calc(100%-14px)] bg-(--color-commissioner)'}"
          ></span>
        </span>
        <span>{isDark ? "Dark" : "Light"}</span>
      </button>
    </div>
  </div>

  <!-- Bottom bar: links + disclaimer -->
  <div class="bg-(--color-surface-inset) py-3">
    <div
      class="mx-auto max-w-7xl px-page flex flex-col sm:flex-row items-center justify-between gap-2"
    >
      <p
        class="font-mono text-[0.6rem] tracking-wide text-(--color-text-muted)"
      >
        Not affiliated with Yahoo, MLB, or any MLB team.
      </p>
      <a
        href="/privacy"
        class="font-mono text-[0.6rem] tracking-wide text-(--color-text-muted) hover:text-(--color-accent-amber) underline underline-offset-2"
      >
        Privacy Policy
      </a>
    </div>
  </div>
</footer>
