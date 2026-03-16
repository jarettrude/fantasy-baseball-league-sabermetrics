<!--
  Free Agents View - Available players for roster moves

  Displays free agents with their value projections, filtered by position.
  Shows season and next-games values to help managers make informed
  waiver wire decisions.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { fetchUser, getUser } from "../lib/stores.svelte";
  import { navigate } from "astro:transitions/client";

  let data: any = $state(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let posFilter = $state("");

  let currentPage = $state(1);
  let pageSize = $state(50);

  type SortKey =
    | "name"
    | "position"
    | "team"
    | "y_rank"
    | "our_rank"
    | "season_value"
    | "next_value"
    | "xstat"
    | "roster_percent";
  let sortKey = $state<SortKey>("season_value");
  let sortDir = $state<1 | -1>(-1);

  /**
   * Applies sort rules sequentially based on table header events.
   * Flips polarity if the active key matches the new key.
   * @param {SortKey} key - Property binding to sort logic
   */
  function sort(key: SortKey) {
    if (sortKey === key) {
      sortDir = sortDir === 1 ? -1 : 1;
    } else {
      sortKey = key;
      sortDir = key === "name" || key === "position" || key === "team" ? 1 : -1;
    }
  }

  /**
   * Normalizes nested attributes from API json schemas for sort comparator loops.
   * @param {any} fa - A free agent entity schema
   * @param {SortKey} key - Evaluation property
   * @returns {string | number}
   */
  function getVal(fa: any, key: SortKey): string | number {
    switch (key) {
      case "name":
        return fa.player.name;
      case "position":
        return fa.player.primary_position;
      case "team":
        return fa.player.team_abbr || "";
      case "y_rank":
        return Number(fa.season_value?.yahoo_rank ?? 9999);
      case "our_rank":
        return Number(fa.season_value?.our_rank ?? 9999);
      case "season_value":
        return Number(fa.season_value?.composite_value ?? -99);
      case "next_value":
        return Number(fa.next_games_value?.composite_value ?? -99);
      case "roster_percent":
        return Number(fa.season_value?.roster_percent ?? -1);
      case "xstat": {
        const isPitcher = ["SP", "RP", "P"].includes(
          fa.player.primary_position,
        );
        return isPitcher
          ? Number(fa.season_value?.xera ?? 999)
          : Number(fa.season_value?.xwoba ?? -99);
      }
    }
  }

  function toNumber(value: unknown): number | null {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatTrend(value: unknown): string | null {
    const numeric = toNumber(value);
    return numeric === null ? null : numeric.toFixed(1);
  }

  let sortedFA = $derived(
    data?.free_agents
      ? [...data.free_agents].sort((a, b) => {
          const av = getVal(a, sortKey);
          const bv = getVal(b, sortKey);
          if (av < bv) return -1 * sortDir;
          if (av > bv) return 1 * sortDir;
          return 0;
        })
      : [],
  );

  let totalPages = $derived(Math.ceil(sortedFA.length / pageSize));

  let paginatedFA = $derived(
    sortedFA.slice((currentPage - 1) * pageSize, currentPage * pageSize),
  );

  /**
   * Calculates structural text string for sort chevrons.
   * @param {SortKey} key - The active column property target
   * @returns {string} Text symbol representations
   */
  function sortIcon(key: SortKey): string {
    if (sortKey !== key) return "";
    return sortDir === -1 ? " ▼" : " ▲";
  }

  const positions = ["", "C", "1B", "2B", "3B", "SS", "OF", "SP", "RP", "Util"];

  onMount(async () => {
    await fetchUser();
    if (!getUser()) {
      navigate("/login", { history: "replace" });
      return;
    }
    const savedFilter = sessionStorage.getItem("moose_posFilter");
    if (savedFilter !== null) posFilter = savedFilter;
    await loadData();
  });

  /**
   * Initiates payload fetch for external un-owned entities.
   * Enforces pagination reset for strict filtering alignment.
   */
  async function loadData() {
    loading = true;
    error = null;
    currentPage = 1;
    sessionStorage.setItem("moose_posFilter", posFilter);
    try {
      const params: Record<string, string> = {};
      if (posFilter) params.position = posFilter;
      data = await api.get("/players/free-agents", params);
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  /**
   * Implements a 24-hour expiration threshold calculation.
   * @param {string | null} ts - ISO timestamp string of last cron execution
   * @returns {boolean} True if data exceeds safe threshold limit
   */
  function freshnessWarning(ts: string | null): boolean {
    if (!ts) return true;
    return Date.now() - new Date(ts).getTime() > 24 * 60 * 60 * 1000;
  }

  /**
   * Sanitizes timestamp rendering.
   * @param {string | null} ts - ISO string
   * @returns {string} Human readable formatted locale string
   */
  function formatTs(ts: string | null): string {
    if (!ts) return "Never";
    return new Date(ts).toLocaleString();
  }

  let addCandidates = $derived(
    data?.free_agents
      ? [...data.free_agents]
          .filter((f: any) => f.season_value !== null)
          .sort(
            (a: any, b: any) =>
              Number(b.season_value?.composite_value ?? -99) -
              Number(a.season_value?.composite_value ?? -99),
          )
          .slice(0, 3)
      : [],
  );
</script>

<div class="flex flex-wrap gap-2 mb-6">
  {#each positions as pos}
    <button
      onclick={() => {
        posFilter = pos;
        loadData();
      }}
      class="rounded-sm px-3 py-1.5 font-mono text-xs font-bold tracking-wide transition cursor-pointer {posFilter ===
      pos
        ? 'bg-(--color-commissioner) text-(--color-text-inverse)'
        : 'bg-(--color-surface-alt) text-(--color-text-muted) hover:bg-(--color-surface-raised) hover:text-(--color-text)'}"
    >
      {pos || "All"}
    </button>
  {/each}
</div>

{#if loading}
  <div class="space-y-3">
    <div class="card overflow-hidden">
      {#each [1, 2, 3, 4, 5] as _}
        <div
          class="flex items-center gap-4 px-4 py-3 border-b border-(--color-border-subtle)"
        >
          <div class="skeleton h-4 w-28"></div>
          <div class="skeleton h-4 w-16"></div>
        </div>
      {/each}
    </div>
  </div>
{:else if error}
  <div
    class="card p-6 border-l-4 border-l-(--color-danger) font-mono text-sm text-(--color-danger)"
  >
    {error}
  </div>
{:else if data}
  <div class="mb-6">
    <div class="flex flex-col gap-1 text-right">
      <div class="hidden"></div>
      <div class="hidden"></div>
      <div class="font-mono text-[0.6rem] text-(--color-text-muted)">
        {#if freshnessWarning(data.snapshot_at)}
          <span
            title="Data may be out of date."
            class="text-(--color-warning) font-bold"
          >
            [! OUT OF DATE]
          </span>
        {/if}
        <span
          >Free Agents Data: <span class="text-(--color-text-muted)"
            >{formatTs(data.snapshot_at)}</span
          ></span
        >
      </div>
    </div>
  </div>

  {#if data.free_agents.length === 0}
    <div
      class="card p-6 text-center font-mono text-sm text-(--color-text-muted)"
    >
      No free agents found. Try a different filter or wait for sync.
    </div>
  {:else}
    {#if addCandidates.length > 0}
      <div class="card p-5 mb-6 border-l-4 border-l-(--color-success)">
        <div class="mb-3">
          <span
            class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-success)"
          >
            SYSTEM ALERT
          </span>
          <p class="font-mono text-[0.6rem] text-(--color-text-muted) mt-0.5">
            // TOP ADD RECOMMENDATIONS
          </p>
        </div>
        <div class="flex flex-wrap gap-3">
          {#each addCandidates as fa}
            <div
              class="flex items-center gap-2 rounded-sm bg-(--color-success-muted) border border-(--color-success)/30 px-3 py-2"
            >
              <div class="font-display text-sm font-bold text-(--color-text)">
                {fa.player.name}
              </div>
              <div class="font-mono text-xs font-bold text-(--color-success)">
                {Number(fa.season_value?.composite_value ?? 0).toFixed(2)}
              </div>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <div class="card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-(--color-border)">
            <th class="px-3 py-2.5 text-left">
              <button
                onclick={() => sort("name")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer whitespace-nowrap"
                title="Player Name"
              >
                Player{sortIcon("name")}
              </button>
            </th>
            <th class="px-2 py-2.5 text-center">
              <button
                onclick={() => sort("y_rank")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Official Yahoo Fantasy Rank based on season-to-date stats"
              >
                Y! Rank{sortIcon("y_rank")}
              </button>
            </th>
            <th class="px-2 py-2.5 text-center">
              <button
                onclick={() => sort("our_rank")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Internal Moose ranking based on customized z-score valuation"
              >
                Our Rank{sortIcon("our_rank")}
              </button>
            </th>
            <th class="px-2 py-2.5 text-center">
              <button
                onclick={() => sort("next_value")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Forecasted value over the next 7 days. Adjusted for missed games, 2-start pitchers, and Vegas Implied Win Odds (Matchups)."
              >
                Next Val{sortIcon("next_value")}
              </button>
            </th>
            <th class="px-2 py-2.5 text-center hidden sm:table-cell">
              <button
                onclick={() => sort("position")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Player's Primary Position"
              >
                Pos{sortIcon("position")}
              </button>
            </th>
            <th
              class="px-2 py-2.5 text-center font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hidden md:table-cell"
              title="Official MLB Injury Status (e.g., IL10, IL60, DTD, ACTIVE)"
              >Status</th
            >
            <th class="px-2 py-2.5 text-center hidden md:table-cell">
              <button
                onclick={() => sort("team")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="MLB Team Abbreviation"
              >
                Team{sortIcon("team")}
              </button>
            </th>
            <th class="px-2 py-2.5 text-center hidden lg:table-cell">
              <button
                onclick={() => sort("season_value")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Internal Moose ranking based on customized z-score valuation"
              >
                Int{sortIcon("season_value")}
              </button>
            </th>
            <th class="px-2 py-2.5 text-center hidden xl:table-cell">
              <button
                onclick={() => sort("xstat")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Statcast Expected Metrics (xwOBA for hitters, xERA for pitchers)"
              >
                xStat{sortIcon("xstat")}
              </button>
            </th>
            <th class="px-2 py-2.5 text-center hidden xl:table-cell">
              <button
                onclick={() => sort("roster_percent")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Yahoo Roster Percentage & 3-Day Trend"
              >
                Rost%{sortIcon("roster_percent")}
              </button>
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-(--color-border-subtle)">
          {#each paginatedFA as fa}
            <tr class="hover:bg-(--color-surface-raised)">
              <td
                class="px-3 py-2.5 font-display text-sm font-bold text-(--color-text) whitespace-nowrap"
                >{fa.player.name}</td
              >
              <td
                class="px-2 py-2.5 text-center font-mono text-xs text-(--color-text-muted)"
                >{fa.season_value?.yahoo_rank || "-"}</td
              >
              <td
                class="px-2 py-2.5 text-center font-mono text-xs text-(--color-text-muted)"
                >{fa.season_value?.our_rank || "-"}</td
              >
              <td
                class="px-2 py-2.5 text-center font-mono text-xs font-bold {Number(
                  fa.next_games_value?.composite_value ?? 0,
                ) > 0
                  ? 'text-(--color-success)'
                  : Number(fa.next_games_value?.composite_value ?? 0) < 0
                    ? 'text-(--color-danger)'
                    : 'text-(--color-text-muted)'}"
              >
                {fa.next_games_value
                  ? Number(fa.next_games_value.composite_value).toFixed(2)
                  : "-"}
              </td>
              <td
                class="px-2 py-2.5 text-center font-mono text-xs text-(--color-text-muted) hidden sm:table-cell"
                >{fa.player.primary_position}</td
              >
              <td class="px-2 py-2.5 text-center hidden md:table-cell">
                {#if fa.player.injury_status}
                  <span class="badge badge-warning"
                    >{fa.player.injury_status}</span
                  >
                {:else}
                  <span class="badge badge-success">Available</span>
                {/if}
              </td>
              <td
                class="px-2 py-2.5 text-center font-mono text-xs text-(--color-text-muted) hidden md:table-cell"
                >{fa.player.team_abbr || "-"}</td
              >
              <td
                class="px-2 py-2.5 text-center font-mono text-xs font-bold hidden lg:table-cell {Number(
                  fa.season_value?.composite_value ?? 0,
                ) > 0
                  ? 'text-(--color-success)'
                  : Number(fa.season_value?.composite_value ?? 0) < 0
                    ? 'text-(--color-danger)'
                    : 'text-(--color-text-muted)'}"
              >
                {fa.season_value
                  ? Number(fa.season_value.composite_value).toFixed(2)
                  : "-"}
              </td>
              <td
                class="px-2 py-2.5 text-center font-mono text-xs text-(--color-text-muted) hidden xl:table-cell"
              >
                {#if fa.season_value}
                  {#if ["SP", "RP", "P"].includes(fa.player.primary_position)}
                    {#if fa.season_value.xera !== null && fa.season_value.xera !== undefined}
                      <span title="Expected ERA (xERA) from Statcast"
                        >{fa.season_value.xera.toFixed(2)}</span
                      >
                    {:else}
                      -
                    {/if}
                  {:else if fa.season_value.xwoba !== null && fa.season_value.xwoba !== undefined}
                    <span title="Expected wOBA (xwOBA) from Statcast"
                      >{fa.season_value.xwoba.toFixed(3)}</span
                    >
                  {:else}
                    -
                  {/if}
                {:else}
                  -
                {/if}
              </td>
              <td class="px-2 py-2.5 text-center hidden xl:table-cell">
                {#if fa.season_value?.roster_percent !== null && fa.season_value?.roster_percent !== undefined}
                  <div class="flex items-center justify-center gap-1">
                    <span class="font-mono text-xs text-(--color-text-muted)"
                      >{fa.season_value.roster_percent}%</span
                    >
                    {#if toNumber(fa.season_value.roster_trend) !== null && toNumber(fa.season_value.roster_trend)! > 0}
                      <span
                        class="font-mono text-[0.6rem] text-(--color-success)"
                        >+{formatTrend(fa.season_value.roster_trend)}</span
                      >
                    {:else if toNumber(fa.season_value.roster_trend) !== null && toNumber(fa.season_value.roster_trend)! < 0}
                      <span
                        class="font-mono text-[0.6rem] text-(--color-danger)"
                        >{formatTrend(fa.season_value.roster_trend)}</span
                      >
                    {:else}
                      <span
                        class="font-mono text-[0.6rem] text-(--color-text-muted)"
                        >—</span
                      >
                    {/if}
                  </div>
                {:else}
                  <span class="font-mono text-xs text-(--color-text-muted)"
                    >-</span
                  >
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <div
      class="flex flex-col sm:flex-row items-center justify-between gap-3 mt-4"
    >
      <div class="font-mono text-[0.6rem] text-(--color-text-muted)">
        Showing {(currentPage - 1) * pageSize + 1} to {Math.min(
          currentPage * pageSize,
          sortedFA.length,
        )} of {sortedFA.length} players
        {#if data.snapshot_at}
          <span class="mx-1 text-(--color-border)">|</span> Last synced: {new Date(
            data.snapshot_at,
          ).toLocaleString()}
        {/if}
      </div>

      {#if totalPages > 1}
        <div class="flex items-center gap-2">
          <button
            class="btn btn-secondary px-3 py-1 text-xs"
            disabled={currentPage === 1}
            onclick={() => (currentPage -= 1)}
          >
            Prev
          </button>
          <div
            class="font-mono text-xs font-bold text-(--color-text-muted) px-2"
          >
            {currentPage} / {totalPages}
          </div>
          <button
            class="btn btn-secondary px-3 py-1 text-xs"
            disabled={currentPage === totalPages}
            onclick={() => (currentPage += 1)}
          >
            Next
          </button>
        </div>
      {/if}
    </div>
  {/if}
{/if}
