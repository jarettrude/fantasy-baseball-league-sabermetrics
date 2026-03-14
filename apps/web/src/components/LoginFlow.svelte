<!--
  Login Flow - Yahoo OAuth authentication

  Handles Yahoo OAuth login flow, redirects to Yahoo authorization,
  and manages authentication state. Shows demo mode option when available.
-->
<script lang="ts">
  import { api } from "../lib/api";

  let loading = $state(false);
  let error = $state<string | null>(null);

  /**
   * Initiates the OAuth2 authorization flow with Yahoo.
   * Redirects the browser window synchronously to the returned authorization URL.
   */
  async function startLogin() {
    loading = true;
    error = null;
    try {
      const data = await api.post<{ redirect_url: string }>("/auth/login");
      window.location.href = data.redirect_url;
    } catch (e: any) {
      error = e.message || "Failed to start login";
      loading = false;
    }
  }
</script>

<div class="space-y-5">
  {#if loading}
    <div class="flex items-center gap-3 py-3">
      <div
        class="h-5 w-5 rounded-full border-2 border-(--color-commissioner) border-t-transparent animate-spin"
      ></div>
      <p class="font-mono text-xs text-(--color-text-muted)">
        Connecting to Yahoo...
      </p>
    </div>
  {:else if error}
    <div
      class="rounded-sm border border-(--color-danger) bg-(--color-danger-muted) p-3 font-mono text-xs text-(--color-danger)"
    >
      {error}
    </div>
  {/if}

  <button
    onclick={startLogin}
    disabled={loading}
    class="btn btn-primary w-full"
  >
    {loading ? "Connecting..." : "Continue with Yahoo"}
  </button>

  <p
    class="font-mono text-[0.65rem] text-(--color-text-muted) text-center leading-relaxed"
  >
    Read-only access to your fantasy league data. We never modify your Yahoo
    account.
  </p>
</div>
