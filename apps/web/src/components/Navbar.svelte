<!--
  Navigation Bar - Main site navigation component

  Provides responsive navigation with user authentication state display.
  Includes dropdown menu for user actions and demo mode indicator.
  Handles login/logout flows and redirects appropriately.
-->
<script lang="ts">
  import logo from "../assets/mse_logo.svg";
  import { onMount } from "svelte";
  import {
    fetchUser,
    getIsLoading,
    getUser,
    logout,
  } from "../lib/stores.svelte";
  import { api } from "../lib/api";

  let user = $derived(getUser());
  let isLoading = $derived(getIsLoading());
  let menuOpen = $state(false);
  let demoMode = $state(false);

  onMount(async () => {
    fetchUser();
    try {
      const config = await api.get<{ demo_mode: boolean }>("/config");
      demoMode = config.demo_mode;
    } catch {
      // Non-fatal
    }
  });
</script>

{#if demoMode}
  <div
    class="bg-(--color-warning-muted) border-b border-(--color-warning) px-page py-2 text-center font-mono text-xs font-bold tracking-wide text-(--color-warning) uppercase"
  >
    Demo Mode — data is fictitious and not connected to any real Yahoo league.
  </div>
{/if}

<nav
  class="sticky top-0 z-(--z-nav) bg-(--color-surface)/95 backdrop-blur-md border-b border-(--color-border) stitch-border-top"
>
  <div
    class="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-page"
  >
    <a href="/" class="flex items-center gap-2.5 group">
      <img src={logo.src} alt="MSE Logo" class="h-8 w-8 rounded-sm" />
      <span
        class="font-display text-sm font-bold tracking-tight text-(--color-text) group-hover:text-(--color-accent-amber) hidden sm:inline"
        >Moose Sports Empire</span
      >
    </a>

    {#if isLoading}
      <div class="flex items-center gap-3">
        <div
          class="hidden md:block h-4 w-28 rounded bg-(--color-surface-raised)"
        ></div>
        <div class="h-8 w-20 rounded-sm bg-(--color-surface-raised)"></div>
      </div>
    {:else if user}
      <div class="hidden md:flex items-center gap-1">
        <a
          href="/dashboard"
          class="px-3 py-1.5 rounded-sm font-mono text-xs font-semibold tracking-wide uppercase text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised)"
          >Dashboard</a
        >
        <a
          href="/bench"
          class="px-3 py-1.5 rounded-sm font-mono text-xs font-semibold tracking-wide uppercase text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised)"
          >Team Bench</a
        >
        <a
          href="/faq"
          class="px-3 py-1.5 rounded-sm font-mono text-xs font-semibold tracking-wide uppercase text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised)"
          >FAQ</a
        >
        <a
          href="/commish-notes"
          class="px-3 py-1.5 rounded-sm font-mono text-xs font-semibold tracking-wide uppercase text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised)"
          >Commish Notes</a
        >
        {#if user.role === "commissioner"}
          <a
            href="/admin"
            class="px-3 py-1.5 rounded-sm font-mono text-xs font-bold tracking-wide uppercase text-(--color-commissioner) hover:bg-(--color-commissioner-muted)"
            >Admin</a
          >
        {/if}
      </div>

      <div class="flex items-center gap-3">
        <span
          class="hidden lg:inline font-mono text-xs text-(--color-text-muted)"
          >{user.display_name}</span
        >
        {#if user.role === "commissioner"}
          <span class="badge badge-commissioner hidden lg:inline-flex"
            >Commissioner</span
          >
        {/if}
        <button
          onclick={() => logout()}
          class="btn btn-ghost btn-sm hidden md:inline-flex"
        >
          Logout
        </button>
        <button
          onclick={() => (menuOpen = !menuOpen)}
          class="md:hidden p-2 rounded-sm text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised)"
          aria-label="Toggle menu"
          aria-expanded={menuOpen}
        >
          <svg
            class="h-5 w-5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M4 6h16M4 12h16M4 18h16"
            />
          </svg>
        </button>
      </div>
    {:else}
      <div class="flex items-center gap-3">
        <a
          href="/faq"
          class="px-3 py-1.5 rounded-sm font-mono text-xs font-semibold tracking-wide uppercase text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised)"
          >FAQ</a
        >
        <a href="/login" class="btn btn-primary"> Sign in with Yahoo </a>
      </div>
    {/if}
  </div>

  {#if menuOpen && user}
    <div
      class="md:hidden border-t border-(--color-border) bg-(--color-surface) px-page py-3 flex flex-col gap-1 animate-fade-in"
    >
      <a
        href="/dashboard"
        class="px-3 py-2.5 rounded-sm font-mono text-xs font-semibold tracking-wide uppercase text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised)"
        >Dashboard</a
      >
      <a
        href="/bench"
        class="px-3 py-2.5 rounded-sm font-mono text-xs font-semibold tracking-wide uppercase text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised)"
        >Team Bench</a
      >
      <a
        href="/faq"
        class="px-3 py-2.5 rounded-sm font-mono text-xs font-semibold tracking-wide uppercase text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised)"
        >FAQ</a
      >
      <a
        href="/commish-notes"
        class="px-3 py-2.5 rounded-sm font-mono text-xs font-semibold tracking-wide uppercase text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised)"
        >Commish Notes</a
      >
      {#if user.role === "commissioner"}
        <a
          href="/admin"
          class="px-3 py-2.5 rounded-sm font-mono text-xs font-bold tracking-wide uppercase text-(--color-commissioner) hover:bg-(--color-commissioner-muted)"
          >Admin</a
        >
      {/if}
      <div class="border-t border-(--color-border-subtle) mt-2 pt-2">
        <button
          onclick={() => logout()}
          class="btn btn-ghost btn-sm w-full justify-start"
        >
          Logout
        </button>
      </div>
    </div>
  {/if}
</nav>
