<!--
  Bench View - Manager's roster and player values

  Displays the authenticated manager's active roster with season and
  next-games value projections. Includes manager briefings and
  player value trend information.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { fetchUser, getUser } from "../lib/stores.svelte";
  import { navigate } from "astro:transitions/client";

  let data: any = $state(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

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
   * Translates header clicks into localized component sorting state logic.
   * Reverses sort direction if clicking the already-active sort key.
   * @param {SortKey} key - The data attribute identifier to mutate
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
   * Normalizes nested JSON payload logic for table comparators.
   * Resolves fallback edge cases natively.
   * @param {any} slot - Roster slot data structure
   * @param {SortKey} key - Required value key
   * @returns {string | number} Normalized comparative value
   */
  function getVal(slot: any, key: SortKey): string | number {
    switch (key) {
      case "name":
        return slot.player.name;
      case "position":
        return slot.player.primary_position;
      case "team":
        return slot.player.team_abbr || "";
      case "y_rank":
        return Number(slot.season_value?.yahoo_rank ?? 9999);
      case "our_rank":
        return Number(slot.season_value?.our_rank ?? 9999);
      case "season_value":
        return Number(slot.season_value?.composite_value ?? -99);
      case "next_value":
        return Number(slot.next_games_value?.composite_value ?? -99);
      case "roster_percent":
        return Number(slot.season_value?.roster_percent ?? -1);
      case "xstat": {
        const isPitcher = ["SP", "RP", "P"].includes(
          slot.player.primary_position,
        );
        return isPitcher
          ? Number(slot.season_value?.xera ?? 999)
          : Number(slot.season_value?.xwoba ?? -99);
      }
    }
  }

  let sortedRoster = $derived(
    data?.roster
      ? [...data.roster].sort((a, b) => {
          const av = getVal(a, sortKey);
          const bv = getVal(b, sortKey);
          if (av < bv) return -1 * sortDir;
          if (av > bv) return 1 * sortDir;
          return 0;
        })
      : [],
  );

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

  let dropCandidates = $derived(
    sortedRoster
      .filter((s: any) => s.season_value !== null)
      .sort(
        (a: any, b: any) =>
          Number(a.season_value?.composite_value ?? 0) -
          Number(b.season_value?.composite_value ?? 0),
      )
      .slice(0, 3),
  );

  onMount(async () => {
    await fetchUser();
    if (!getUser()) {
      navigate("/login", { history: "replace" });
      return;
    }
    try {
      data = await api.get("/players/bench");

      if (data?.briefing && !data.briefing.is_viewed) {
        try {
          window.dispatchEvent(
            new CustomEvent("toast", {
              detail: {
                type: "info",
                message: "New morning briefing available",
                duration: 5000,
              },
            }),
          );
        } catch (error) {
          console.error("Failed to show toast:", error);
        }
        try {
          await api.post("/players/briefings/read");
        } catch (e) {
          console.error("Failed to mark briefing read", e);
        }
      }
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  });

  /**
   * Connects generic injury strings from external APIs to UI logic semantics.
   * @param {string | null} status - API status indicator string value
   * @returns {string} Tailwind color utility string representation class
   */
  function injuryBadgeClass(status: string | null): string {
    if (!status) return "stat-badge bg-success/30 text-success";
    if (status === "IL60" || status === "OUT")
      return "stat-badge bg-danger/30 text-danger";
    if (status === "IL10" || status === "DTD")
      return "stat-badge bg-warning/30 text-warning";
    return "stat-badge bg-(--color-surface-alt) text-(--color-text-muted)";
  }

  /**
   * Dynamically renders ascii vector chevrons mapped to interactive sort orientations.
   * @param {SortKey} key - Currently asserted target key loop evaluation
   * @returns {string} Literal text marker suffix
   */
  function sortIcon(key: SortKey): string {
    if (sortKey !== key) return "";
    return sortDir === -1 ? " ▼" : " ▲";
  }
</script>

{#if loading}
  <div class="space-y-3">
    <div class="card overflow-hidden">
      {#each [1, 2, 3, 4, 5] as _}
        <div
          class="flex items-center gap-4 px-4 py-3 border-b border-(--color-border-subtle)"
        >
          <div class="skeleton h-4 w-28"></div>
          <div class="skeleton h-4 w-12"></div>
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
  <div
    class="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-6"
  >
    <div>
      <div
        class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-commissioner) mb-1"
      >
        Front Office
      </div>
      <p
        class="font-mono text-[0.6rem] text-(--color-text-muted) tracking-wide"
      >
        Target Organization
      </p>
      <p
        class="font-display text-2xl font-extrabold text-(--color-text) tracking-tight"
      >
        {data.team_name}
      </p>
    </div>
    <div class="flex flex-col gap-1 text-right">
      <div class="hidden"></div>
      <div class="hidden"></div>
      <div class="font-mono text-[0.6rem] text-(--color-text-muted)">
        {#if freshnessWarning(data.season_value_updated)}
          <span
            title="Data may be out of date."
            class="text-(--color-warning) font-bold"
          >
            [! OUT OF DATE]
          </span>
        {/if}
        <span
          >Season Value: <span class="text-(--color-text-muted)"
            >{formatTs(data.season_value_updated)}</span
          ></span
        >
      </div>
      <div class="font-mono text-[0.6rem] text-(--color-text-muted)">
        {#if freshnessWarning(data.next_games_value_updated)}
          <span
            title="Data may be out of date."
            class="text-(--color-warning) font-bold"
          >
            [! OUT OF DATE]
          </span>
        {/if}
        <span
          >Next(7) Value: <span class="text-(--color-text-muted)"
            >{formatTs(data.next_games_value_updated)}</span
          ></span
        >
      </div>
    </div>
  </div>

  {#if data.briefing}
    <div class="card p-5 mb-6 border-l-4 border-l-(--color-info)">
      <div class="mb-3">
        <span
          class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-info)"
        >
          MORNING BRIEFING
        </span>
        <p class="font-mono text-[0.6rem] text-(--color-text-muted) mt-0.5">
          // DATE: {data.briefing.date}
        </p>
      </div>
      <div class="prose prose-sm">
        {@html data.briefing.content || ""}
      </div>
    </div>
  {/if}

  {#if dropCandidates.length > 0}
    <div class="card p-5 mb-6 border-l-4 border-l-(--color-warning)">
      <div class="mb-3">
        <span
          class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-warning)"
        >
          ROSTER ALERT
        </span>
        <p class="font-mono text-[0.6rem] text-(--color-text-muted) mt-0.5">
          // ROSTER INEFFICIENCIES DETECTED
        </p>
      </div>
      <div class="flex flex-wrap gap-3">
        {#each dropCandidates as slot}
          <div
            class="flex items-center gap-2 rounded-sm bg-(--color-warning-muted) border border-(--color-warning)/30 px-3 py-2"
          >
            <div class="font-display text-sm font-bold text-(--color-text)">
              {slot.player.name}
            </div>
            <div class="font-mono text-xs font-bold text-(--color-warning)">
              {Number(slot.season_value?.composite_value ?? 0).toFixed(2)}
            </div>
          </div>
        {/each}
      </div>
    </div>
  {/if}

  {#if data.roster.length === 0}
    <div
      class="card p-6 text-center font-mono text-sm text-(--color-text-muted)"
    >
      // NO ROSTER DATA // SYNC REQUIRED //
    </div>
  {:else}
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
                onclick={() => sort("position")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Player's Primary Position"
              >
                Pos{sortIcon("position")}
              </button>
            </th>
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
                onclick={() => sort("y_rank")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Official Yahoo Fantasy Rank based on season-to-date stats"
              >
                Y!{sortIcon("y_rank")}
              </button>
            </th>
            <th class="px-2 py-2.5 text-center hidden lg:table-cell">
              <button
                onclick={() => sort("our_rank")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Internal Moose ranking based on customized z-score valuation"
              >
                Int{sortIcon("our_rank")}
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
            <th class="px-2 py-2.5 text-center hidden xl:table-cell">
              <button
                onclick={() => sort("xstat")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Expected stats (xwOBA for Hitters, xERA for Pitchers)"
              >
                xStat{sortIcon("xstat")}
              </button>
            </th>
            <th
              class="px-2 py-2.5 text-center hidden sm:table-cell font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
              title="Current slot this player occupies on your fantasy roster"
              >Slot</th
            >
            <th class="px-2 py-2.5 text-center">
              <button
                onclick={() => sort("season_value")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Aggregate value mathematically calculated over the entire season"
              >
                Season{sortIcon("season_value")}
              </button>
            </th>
            <th class="px-2 py-2.5 text-center hidden sm:table-cell">
              <button
                onclick={() => sort("next_value")}
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) hover:text-(--color-text) cursor-pointer"
                title="Forecasted value over the next 7 days. Adjusted for missed games, 2-start pitchers, and Vegas Implied Win Odds (Matchups)."
              >
                Next(7){sortIcon("next_value")}
              </button>
            </th>
            <th
              class="px-2 py-2.5 text-center font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
              title="Official MLB Injury Status (e.g., IL10, IL60, DTD, ACTIVE)"
              >Stat</th
            >
          </tr>
        </thead>
        <tbody class="divide-y divide-(--color-border-subtle)">
          {#each sortedRoster as slot}
            <tr class="hover:bg-(--color-surface-raised)">
              <td
                class="px-3 py-2.5 font-display text-sm font-bold text-(--color-text) whitespace-nowrap"
                >{slot.player.name}</td
              >
              <td
                class="px-2 py-2.5 text-center font-mono text-xs text-(--color-text-muted)"
                >{slot.player.primary_position}</td
              >
              <td
                class="px-2 py-2.5 text-center font-mono text-xs text-(--color-text-muted) hidden md:table-cell"
                >{slot.player.team_abbr || "—"}</td
              >
              <td
                class="px-2 py-2.5 text-center font-mono text-xs text-(--color-text-muted) hidden lg:table-cell"
              >
                {slot.season_value?.yahoo_rank || "—"}
              </td>
              <td
                class="px-2 py-2.5 text-center font-mono text-xs text-(--color-text-muted) hidden lg:table-cell"
              >
                {slot.season_value?.our_rank || "—"}
              </td>
              <td class="px-2 py-2.5 text-center hidden xl:table-cell">
                {#if slot.season_value?.roster_percent !== null && slot.season_value?.roster_percent !== undefined}
                  <div class="flex items-center justify-center gap-1">
                    <span class="font-mono text-xs text-(--color-text-muted)"
                      >{slot.season_value.roster_percent}%</span
                    >
                    {#if slot.season_value.roster_trend !== null && slot.season_value.roster_trend !== undefined && slot.season_value.roster_trend > 0}
                      <span
                        class="font-mono text-[0.6rem] text-(--color-success)"
                        >+{slot.season_value.roster_trend.toFixed(1)}</span
                      >
                    {:else if slot.season_value.roster_trend !== null && slot.season_value.roster_trend !== undefined && slot.season_value.roster_trend < 0}
                      <span
                        class="font-mono text-[0.6rem] text-(--color-danger)"
                        >{slot.season_value.roster_trend.toFixed(1)}</span
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
                    >—</span
                  >
                {/if}
              </td>
              <td
                class="px-2 py-2.5 text-center font-mono text-xs text-(--color-text-muted) hidden xl:table-cell"
              >
                {#if slot.season_value}
                  {#if ["SP", "RP", "P"].includes(slot.player.primary_position)}
                    {#if slot.season_value.xera !== null && slot.season_value.xera !== undefined}
                      <span title="Expected ERA (xERA) from Statcast"
                        >{slot.season_value.xera.toFixed(2)}</span
                      >
                    {:else}
                      —
                    {/if}
                  {:else if slot.season_value.xwoba !== null && slot.season_value.xwoba !== undefined}
                    <span title="Expected wOBA (xwOBA) from Statcast"
                      >{slot.season_value.xwoba.toFixed(3)}</span
                    >
                  {:else}
                    —
                  {/if}
                {:else}
                  —
                {/if}
              </td>
              <td class="px-2 py-2.5 text-center hidden sm:table-cell">
                <span class="badge">{slot.position}</span>
              </td>
              <td
                class="px-2 py-2.5 text-center font-mono text-xs font-bold {Number(
                  slot.season_value?.composite_value ?? 0,
                ) > 0
                  ? 'text-(--color-success)'
                  : Number(slot.season_value?.composite_value ?? 0) < 0
                    ? 'text-(--color-danger)'
                    : 'text-(--color-text-muted)'}"
              >
                {slot.season_value
                  ? Number(slot.season_value.composite_value).toFixed(2)
                  : "—"}
              </td>
              <td
                class="px-2 py-2.5 text-center font-mono text-xs font-bold hidden sm:table-cell {Number(
                  slot.next_games_value?.composite_value ?? 0,
                ) > 0
                  ? 'text-(--color-success)'
                  : Number(slot.next_games_value?.composite_value ?? 0) < 0
                    ? 'text-(--color-danger)'
                    : 'text-(--color-text-muted)'}"
              >
                {slot.next_games_value
                  ? Number(slot.next_games_value.composite_value).toFixed(2)
                  : "—"}
              </td>
              <td class="px-2 py-2.5 text-center">
                <span
                  class={injuryBadgeClass(slot.player.injury_status).replace(
                    "stat-badge",
                    "font-mono text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded-sm border",
                  )}
                >
                  {slot.player.injury_status ?? "ACTIVE"}
                </span>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
{/if}
