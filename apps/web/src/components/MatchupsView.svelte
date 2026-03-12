<!--
  Matchups View - Weekly head-to-head matchups

  Shows weekly matchup results with category breakdowns, team stats,
  and winner information. Supports week selection and historical viewing.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";

  let { week = undefined }: { week?: number } = $props();

  let data: any = $state(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  onMount(async () => {
    try {
      const params: Record<string, string> = {};
      if (week !== undefined) params.week = String(week);
      data = await api.get("/league/matchups", params);
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  });

  function formatTs(ts: string | null): string {
    if (!ts) return "Never";
    return new Date(ts).toLocaleString();
  }
</script>

{#if loading}
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
    {#each [1, 2, 3, 4] as _}
      <div class="card p-6">
        <div class="flex items-center justify-between">
          <div class="skeleton h-6 w-24"></div>
          <div class="skeleton h-4 w-8"></div>
          <div class="skeleton h-6 w-24"></div>
        </div>
      </div>
    {/each}
  </div>
{:else if error}
  <div class="card p-6 border-l-4 border-l-(--color-danger) font-mono text-sm text-(--color-danger)">{error}</div>
{:else if data}
  <div
    class="flex items-center justify-between mb-6"
  >
    <span
      class="font-display text-2xl font-extrabold tracking-tight text-(--color-text)"
      >WEEK {data.week}</span
    >
    {#if data.generated_at}
      <div
        class="font-mono text-[0.6rem] text-(--color-text-muted)"
      >
        // Matchups Scoreboard: {formatTs(data.generated_at)}
      </div>
    {/if}
  </div>
  {#if data.matchups.length === 0}
    <div
      class="card p-6 text-center font-mono text-sm text-(--color-text-muted)"
    >
      // NO DATA DETECTED //
    </div>
  {:else}
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      {#each data.matchups as matchup}
        <div
          class="card overflow-hidden"
        >
          <div class="flex">
            <div
              class="relative flex flex-1 flex-col items-center justify-center p-4 {matchup.team_a_wins >
              matchup.team_b_wins
                ? 'bg-(--color-success-muted)'
                : ''}"
            >
              {#if matchup.team_a_wins > matchup.team_b_wins}
                <div
                  class="absolute top-0 left-0 right-0 h-0.5 bg-(--color-success)"
                ></div>
              {/if}
              <div class="flex flex-col items-center gap-1.5 mb-1">
                {#if matchup.team_a_logo}
                  <img
                    src={matchup.team_a_logo}
                    alt=""
                    class="h-8 w-8 rounded-sm"
                  />
                {/if}
                <p
                  class="font-display text-xs font-bold text-(--color-text) text-center leading-tight"
                >
                  {matchup.team_a_name}
                </p>
              </div>
              <p
                class="mt-1 font-mono text-3xl sm:text-4xl font-black {matchup.team_a_wins >
                matchup.team_b_wins
                  ? 'text-(--color-success)'
                  : 'text-(--color-text)'}"
              >
                {matchup.team_a_wins}
              </p>
            </div>

            <div
              class="flex items-center justify-center px-2 bg-(--color-surface-inset)"
            >
              <span
                class="font-mono text-[0.6rem] font-bold tracking-widest text-(--color-text-muted) uppercase"
                >VERSUS</span
              >
            </div>

            <div
              class="relative flex flex-1 flex-col items-center justify-center p-4 {matchup.team_b_wins >
              matchup.team_a_wins
                ? 'bg-(--color-success-muted)'
                : ''}"
            >
              {#if matchup.team_b_wins > matchup.team_a_wins}
                <div
                  class="absolute top-0 left-0 right-0 h-0.5 bg-(--color-success)"
                ></div>
              {/if}
              <div class="flex flex-col items-center gap-1.5 mb-1">
                {#if matchup.team_b_logo}
                  <img
                    src={matchup.team_b_logo}
                    alt=""
                    class="h-8 w-8 rounded-sm"
                  />
                {/if}
                <p
                  class="font-display text-xs font-bold text-(--color-text) text-center leading-tight"
                >
                  {matchup.team_b_name}
                </p>
              </div>
              <p
                class="mt-1 font-mono text-3xl sm:text-4xl font-black {matchup.team_b_wins >
                matchup.team_a_wins
                  ? 'text-(--color-success)'
                  : 'text-(--color-text)'}"
              >
                {matchup.team_b_wins}
              </p>
            </div>
          </div>

          <div
            class="flex items-center justify-center gap-2 py-1.5 border-t border-(--color-border-subtle) bg-(--color-surface-inset)"
          >
            {#if matchup.is_complete}
              <span
                class="badge badge-info"
                >FINAL</span
              >
            {:else}
              <span
                class="badge badge-success"
                >LIVE</span
              >
            {/if}
            {#if matchup.ties > 0}
              <span class="badge"
                >{matchup.ties} TIES</span
              >
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
{/if}
