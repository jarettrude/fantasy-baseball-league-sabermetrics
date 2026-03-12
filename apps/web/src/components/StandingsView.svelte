<!--
  Standings View - League standings display

  Displays current league standings with team records, rankings,
  and performance metrics. Supports navigation to team details.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";

  let standings: any = $state(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  onMount(async () => {
    try {
      standings = await api.get("/league/standings");
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
  <div class="space-y-3">
    <div class="card overflow-hidden">
      {#each [1, 2, 3, 4, 5] as _}
        <div class="flex items-center gap-4 px-4 py-3 border-b border-(--color-border-subtle)">
          <div class="skeleton h-7 w-7 rounded-sm"></div>
          <div class="skeleton h-4 w-32"></div>
          <div class="skeleton h-4 w-16"></div>
        </div>
      {/each}
    </div>
  </div>
{:else if error}
  <div class="card p-6 border-l-4 border-l-(--color-danger) font-mono text-sm text-(--color-danger)">{error}</div>
{:else if standings}
  <div
    class="flex items-center justify-between mb-6"
  >
    <div class="flex items-center gap-3">
      <span
        class="font-display text-2xl font-extrabold tracking-tight text-(--color-text)"
        >SEASON {standings.season}</span
      >
      <span
        class="badge"
        >WEEK {standings.current_week}</span
      >
    </div>
    {#if standings.generated_at}
      <div
        class="font-mono text-[0.6rem] text-(--color-text-muted)"
      >
        // Standings Data: {formatTs(standings.generated_at)}
      </div>
    {/if}
  </div>
  <div
    class="card overflow-hidden"
  >
    <table class="w-full text-sm">
      <thead>
        <tr
          class="border-b border-(--color-border)"
        >
          <th class="px-4 py-3 text-center font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted) w-16">Rank</th>
          <th class="px-4 py-3 text-left font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted)">Organization</th>
          <th class="px-3 py-3 text-center font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted)">W</th>
          <th class="px-3 py-3 text-center font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted)">L</th>
          <th class="px-3 py-3 text-center font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted) hidden sm:table-cell">T</th>
          <th class="px-3 py-3 text-center font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted) hidden sm:table-cell">Win %</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-(--color-border-subtle)">
        {#each standings.standings as team, i}
          {@const total = team.wins + team.losses + team.ties}
          {@const winPct =
            total > 0
              ? ((team.wins + team.ties * 0.5) / total).toFixed(3)
              : ".000"}
          <tr class="hover:bg-(--color-surface-raised)">
            <td class="px-4 py-3 text-center">
              <span
                class="inline-flex h-7 w-7 items-center justify-center rounded-sm font-mono font-black text-xs border {i ===
                0
                  ? 'border-(--color-commissioner) bg-(--color-commissioner-muted) text-(--color-commissioner)'
                  : i === 1
                    ? 'border-(--color-text-faint) bg-(--color-surface-raised) text-(--color-text-muted)'
                    : 'border-(--color-border) bg-transparent text-(--color-text-muted)'}"
              >
                {team.standing || i + 1}
              </span>
            </td>
            <td class="px-4 py-3">
              <div class="flex items-center gap-2.5">
                {#if team.logo_url}
                  <img
                    src={team.logo_url}
                    alt=""
                    class="h-7 w-7 rounded-sm"
                  />
                {:else}
                  <div
                    class="flex h-7 w-7 items-center justify-center rounded-sm bg-(--color-surface-raised) border border-(--color-border) font-mono text-xs font-bold text-(--color-text-muted)"
                  >
                    {team.team_name.charAt(0)}
                  </div>
                {/if}
                <span
                  class="font-display text-sm font-bold text-(--color-text)"
                  >{team.team_name}</span
                >
              </div>
            </td>
            <td
              class="px-3 py-3 text-center font-mono text-sm font-bold text-(--color-success)"
              >{team.wins}</td
            >
            <td
              class="px-3 py-3 text-center font-mono text-sm text-(--color-text-muted)"
              >{team.losses}</td
            >
            <td
              class="px-3 py-3 text-center font-mono text-sm text-(--color-text-muted) hidden sm:table-cell"
              >{team.ties}</td
            >
            <td
              class="px-3 py-3 text-center font-mono text-sm font-bold hidden sm:table-cell {parseFloat(
                winPct,
              ) >= 0.5
                ? 'text-(--color-text)'
                : 'text-(--color-text-muted)'}">{winPct.replace(/^0+/, "")}</td
            >
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
