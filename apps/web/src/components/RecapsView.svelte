<!--
  Recaps View - AI-generated content recaps

  Lists AI-generated recaps for league-wide and team-specific content.
  Supports filtering by week and recap type with markdown rendering.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { fetchUser, getUser } from "../lib/stores.svelte";
  import { navigate } from "astro:transitions/client";

  let recapsData: any = $state(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let selectedWeek = $state(0);
  let history = $state<number[]>([]);

  onMount(async () => {
    await fetchUser();
    if (!getUser()) {
      navigate("/login", { history: "replace" });
      return;
    }

    try {
      history = await api.get("/recaps/history");
      if (history.length > 0) {
        selectedWeek = history[0];
      } else {
        const league: any = await api.get("/league/info");
        selectedWeek = Math.max(1, (league.current_week || 2) - 1);
      }
    } catch {
      // fallback
    }
    await loadRecaps();
  });

  /**
   * Fetches artificial intelligence week recaps for the current logged-in manager context.
   */
  async function loadRecaps(weekOverride?: number) {
    if (weekOverride !== undefined) {
      selectedWeek = weekOverride;
    }
    if (selectedWeek === 0) return;

    loading = true;
    error = null;
    try {
      recapsData = await api.get(`/recaps/week/${selectedWeek}`);
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }
</script>

<div class="grid grid-cols-1 lg:grid-cols-[1fr_220px] gap-6">
  <!-- News Stream (Main Content) - Order 1 on mobile, 2 on desktop -->
  <main class="order-2 lg:order-1 min-w-0">
    {#if loading}
      <div class="card p-8">
        <div class="skeleton h-6 w-48 mb-4"></div>
        <div class="space-y-3">
          <div class="skeleton h-4 w-full"></div>
          <div class="skeleton h-4 w-3/4"></div>
          <div class="skeleton h-4 w-5/6"></div>
        </div>
      </div>
    {:else if error}
      <div
        class="card p-6 border-l-4 border-l-(--color-danger) font-mono text-sm text-(--color-danger)"
      >
        {error}
      </div>
    {:else if recapsData}
      {#if recapsData.recaps.length === 0}
        <div
          class="card p-8 text-center font-mono text-sm text-(--color-text-muted)"
        >
          // NO SCOUTING REPORT FOUND FOR WEEK {selectedWeek} //
          <p class="mt-2 text-xs text-(--color-text-muted)">
            REPORTS GENERATED POST-WEEK COMPLETION.
          </p>
        </div>
      {:else}
        <div class="space-y-6">
          {#each recapsData.recaps as recap}
            <div
              class="relative flex flex-col sm:flex-row gap-0 sm:gap-8 border-l-4 {recap.type ===
              'league'
                ? 'border-l-(--color-border-commissioner) bg-(--color-surface)'
                : 'border-l-(--color-text-muted) bg-(--color-surface-base)'} p-8 border border-(--color-border) shadow-xl"
            >
              <div class="shrink-0 sm:w-44">
                <div
                  class="flex flex-wrap items-center gap-2 mb-3 sm:flex-col sm:items-start sm:gap-2"
                >
                  <span
                    class="font-mono text-[0.6rem] font-bold tracking-widest text-(--color-text-muted)"
                    >VOL. 2026 // ISSUE #{recap.week
                      .toString()
                      .padStart(2, "0")}</span
                  >
                  <span
                    class="inline-block px-3 py-1.5 font-mono text-xs font-black uppercase tracking-widest {recap.type ===
                    'league'
                      ? 'bg-(--color-surface-raised) text-(--color-text-commissioner) border border-(--color-border-commissioner)'
                      : 'bg-(--color-surface-alt) text-(--color-text) border border-(--color-border)'}"
                  >
                    {recap.type === "league"
                      ? "LEAGUE REPORT"
                      : `TEAM: ${recap.team_name || "UNKNOWN"}`}
                  </span>
                </div>

                <div
                  class="font-mono text-[0.55rem] text-(--color-text-muted) space-y-0.5 mt-2"
                >
                  <div class="hidden"></div>
                  <div class="hidden"></div>
                  {#if recap.published_at}
                    <div>TS: {new Date(recap.published_at).toISOString()}</div>
                  {/if}
                  <div>STATUS: APPROVED</div>
                </div>
              </div>

              <div class="flex-1 prose prose-sm min-w-0">
                {@html recap.content || "<em>[NO CONTENT]</em>"}
              </div>
            </div>
          {/each}
        </div>
      {/if}
    {/if}
  </main>

  <!-- Dynamic Sidebar Navigation - Order 2 on mobile, 1 on desktop -->
  <aside class="order-1 lg:order-2">
    <div class="mb-3">
      <h3
        class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text-muted)"
      >
        ISSUE ARCHIVE
      </h3>
    </div>

    {#if history.length === 0}
      <div
        class="card p-4 text-center font-mono text-xs text-(--color-text-muted)"
      >
        // NO HISTORY FOUND //
      </div>
    {:else}
      <!-- Compact Grid Layout for many weeks -->
      <div class="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-3 gap-1.5">
        {#each history as week}
          <button
            onclick={() => loadRecaps(week)}
            class="aspect-square flex flex-col items-center justify-center font-mono transition-all duration-200 group border-2
            {selectedWeek === week
              ? 'bg-(--color-accent-primary) text-(--color-text-inverse) border-(--color-accent-amber) shadow-[2px_2px_0px_rgba(100,255,100,0.2)]'
              : 'bg-(--color-surface) text-(--color-text-commissioner) border-(--color-text) hover:border-(--color-accent-amber) hover:text-(--color-text-inverse) hover:bg-(--color-surface-raised)'}}"
            title="Week {week}"
          >
            <span class="text-xs font-bold"
              >#{week.toString().padStart(2, "0")}</span
            >
          </button>
        {/each}
      </div>

      <div class="mt-3">
        <p
          class="font-mono text-[0.55rem] text-(--color-text-muted) leading-relaxed"
        >
          ARCHIVE CONTAINS ALL APPROVED SCOUTING REPORTS FOR THE 2026 SEASON.
          SELECT ISSUE TO RELOAD DATASHEET.
        </p>
      </div>
    {/if}
  </aside>
</div>

<div class="mt-6 text-center">
  <p class="font-mono text-[0.6rem] text-(--color-text-muted) tracking-wide">
    // WEEKLY RECAPS GENERATED WITH AI ASSISTANCE //
  </p>
</div>
