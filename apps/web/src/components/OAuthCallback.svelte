<!--
  OAuth Callback - OAuth response handler

  Processes OAuth callback from Yahoo, exchanges authorization code
  for tokens, and establishes authenticated session with redirect.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { navigate } from "astro:transitions/client";

  let status = $state<"loading" | "success" | "error">("loading");
  let errorMsg = $state("");

  onMount(async () => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");

    if (!code || !state) {
      navigate("/denied?reason=expired", { history: "replace" });
      return;
    }

    try {
      await api.post("/auth/callback", { code, state });
      status = "success";
      setTimeout(() => {
        navigate("/dashboard", { history: "replace" });
      }, 1000);
    } catch (e: any) {
      status = "error";
      let reason = "generic";
      const msg = e.message?.toLowerCase() || "";
      if (msg.includes("initialization")) {
        reason = "initialization";
      } else if (
        msg.includes("access denied") ||
        msg.includes("not a manager")
      ) {
        reason = "unauthorized";
      } else if (msg.includes("tokens") || msg.includes("expired")) {
        reason = "expired";
      }
      navigate(`/denied?reason=${reason}`, { history: "replace" });
    }
  });
</script>

<div>
  {#if status === "loading"}
    <div class="flex flex-col items-center gap-4 py-6 animate-fade-in">
      <div
        class="h-8 w-8 rounded-full border-2 border-(--color-commissioner) border-t-transparent animate-spin"
      ></div>
      <p
        class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text-muted) animate-pulse-dot"
      >
        EXCHANGING CREDENTIALS //
      </p>
    </div>
  {:else if status === "success"}
    <div class="flex flex-col items-center gap-4 py-6 animate-fade-in">
      <div
        class="flex h-12 w-12 items-center justify-center rounded-full bg-(--color-success-muted) border border-(--color-success)"
      >
        <svg
          class="h-6 w-6 text-(--color-success)"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="3"
            d="M5 13l4 4L19 7"
          />
        </svg>
      </div>
      <p
        class="font-mono text-sm font-bold tracking-widest uppercase text-(--color-success)"
      >
        AUTHORIZATION GRANTED //
      </p>
      <p
        class="font-mono text-xs text-(--color-text-muted) animate-pulse-dot"
      >
        ROUTING TO FRONT OFFICE
      </p>
    </div>
  {:else}
    <div class="flex flex-col items-center gap-4 py-6 animate-fade-in">
      <div
        class="flex h-12 w-12 items-center justify-center rounded-full bg-(--color-danger-muted) border border-(--color-danger)"
      >
        <svg
          class="h-6 w-6 text-(--color-danger)"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="3"
            d="M6 18L18 6M6 6l12 12"
          />
        </svg>
      </div>
      <p
        class="font-mono text-sm font-bold tracking-widest uppercase text-(--color-danger)"
      >
        AUTHORIZATION FAILED //
      </p>
      <p
        class="font-mono text-xs text-(--color-text-muted)"
      >
        {errorMsg}
      </p>
      <a
        href="/login"
        class="btn btn-primary mt-2"
      >
        RE-INITIALIZE //
      </a>
    </div>
  {/if}
</div>
