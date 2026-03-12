<!--
  Dashboard View - Main landing page for authenticated users

  Displays league standings, weekly matchups, and quick navigation to other views.
  Automatically refreshes matchup data every 60 seconds during game days.
  Handles authentication state and redirects to login if not authenticated.
-->
<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { api } from "../lib/api";
  import { fetchUser, getUser } from "../lib/stores.svelte";
  import { navigate } from "astro:transitions/client";

  interface MatchupData {
    team_a_name: string;
    team_b_name: string;
    team_a_logo?: string | null;
    team_b_logo?: string | null;
    team_a_stats?: Record<string, number | string>;
    team_b_stats?: Record<string, number | string>;
    team_a_wins: number;
    team_b_wins: number;
    ties: number;
    is_complete?: boolean;
    category_results?: Record<string, MatchupTeamRow["key"]>;
  }

  type MatchupPayload = {
    week: number;
    matchups: MatchupData[];
  };

  type MatchupTeamRow = {
    key: "team_a" | "team_b";
    name: string;
    logo: string | null | undefined;
    stats: Record<string, number | string> | undefined;
    wins: number;
  };

  interface LeagueStatCategory {
    display_name?: string;
  }

  interface LeagueInfoPayload {
    stat_categories?: LeagueStatCategory[];
    start_week?: number;
    current_week?: number;
  }

  interface StandingsEntry {
    team_name?: string | null;
    wins: number;
    losses: number;
    ties: number;
    standing?: number | string | null;
    logo_url?: string | null;
  }

  interface StandingsPayload {
    standings: StandingsEntry[];
    current_week: number;
    league_name?: string | null;
    season?: number;
  }

  let user = $derived(getUser());
  let standings = $state<StandingsPayload | null>(null);
  let leagueInfo = $state<LeagueInfoPayload | null>(null);
  let matchups = $state<MatchupPayload | null>(null);

  const myMatchup = $derived<MatchupData | null>(
    user && matchups?.matchups
      ? (matchups.matchups.find(
          (m) =>
            m.team_a_name.toLowerCase() === user.display_name.toLowerCase() ||
            m.team_b_name.toLowerCase() === user.display_name.toLowerCase(),
        ) ?? null)
      : null,
  );

  const categoryNames = $derived<string[]>(
    Array.isArray(leagueInfo?.stat_categories) &&
      leagueInfo.stat_categories.length
      ? leagueInfo.stat_categories
          .map((cat) => cat.display_name || "")
          .filter(Boolean)
      : Object.keys(myMatchup?.team_a_stats || {}),
  );

  const startWeek = $derived(
    leagueInfo?.start_week ?? standings?.current_week ?? 1,
  );
  const currentWeek = $derived(
    leagueInfo?.current_week ?? matchups?.week ?? standings?.current_week ?? 1,
  );
  const isPreseason = $derived(currentWeek < startWeek);
  const hasMatchupStats = $derived(
    !isPreseason && !!myMatchup && categoryNames.length > 0,
  );
  const teamRows = $derived<MatchupTeamRow[]>(
    myMatchup
      ? [
          {
            key: "team_a",
            name: myMatchup.team_a_name,
            logo: myMatchup.team_a_logo,
            stats: myMatchup.team_a_stats,
            wins: myMatchup.team_a_wins,
          },
          {
            key: "team_b",
            name: myMatchup.team_b_name,
            logo: myMatchup.team_b_logo,
            stats: myMatchup.team_b_stats,
            wins: myMatchup.team_b_wins,
          },
        ]
      : [],
  );

  let loading = $state(true);
  let error = $state<string | null>(null);

  let pollInterval: ReturnType<typeof setInterval> | null = null;

  function formatStatValue(
    stats: Record<string, number | string> | undefined,
    category: string,
  ) {
    if (!stats) return "—";
    const raw = stats[category];
    if (raw === undefined || raw === null || raw === "") {
      return "—";
    }
    if (typeof raw === "number") {
      const fixed = Number(raw.toFixed(3));
      return Number.isInteger(fixed) ? fixed.toString() : fixed.toString();
    }
    return raw;
  }

  function categoryWinner(cat: string, teamKey: MatchupTeamRow["key"]) {
    return myMatchup?.category_results?.[cat] === teamKey;
  }

  function getStandingsEntry(teamName?: string) {
    if (!teamName || !standings?.standings?.length) return null;
    return (
      standings.standings.find(
        (team: any) => team.team_name?.toLowerCase() === teamName.toLowerCase(),
      ) || null
    );
  }

  function getTeamRecord(teamName?: string) {
    const entry = getStandingsEntry(teamName);
    if (!entry) return null;
    return `${entry.wins}-${entry.losses}-${entry.ties}`;
  }

  /**
   * Determines if the current UTC time aligns with peak MLB operational hours (12pm-11:59pm ET).
   * @returns {boolean} True if within active game broadcast window
   */
  function isGameHours(): boolean {
    const now = new Date();
    const easternOffset = -5 * 60;
    const eastMs =
      now.getTime() + (now.getTimezoneOffset() + easternOffset) * 60000;
    const east = new Date(eastMs);
    return east.getHours() >= 12 && east.getHours() <= 23;
  }

  /**
   * Fetches the subset of matchup payload state. Silently ignores errors to permit isolated polling.
   */
  async function loadMatchups() {
    try {
      matchups = (await api.get("/league/matchups")) as MatchupPayload;
    } catch {
      // Non-fatal API omission
    }
  }

  /**
   * Handles parallel resolution of all disparate dashboard widget APIs.
   * Settles the global `loading` state when all streams buffer successfully or reject.
   */
  async function loadAll() {
    try {
      const [standingsResp, leagueInfoResp] = await Promise.all([
        api.get("/league/standings"),
        api.get("/league/info"),
        loadMatchups(),
      ]);
      standings = standingsResp as StandingsPayload;
      leagueInfo = leagueInfoResp as LeagueInfoPayload;
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  onMount(async () => {
    await fetchUser();
    if (!getUser()) {
      navigate("/login", { history: "replace" });
      return;
    }
    await loadAll();

    if (isGameHours()) {
      pollInterval = setInterval(
        () => {
          loadMatchups();
        },
        15 * 60 * 1000,
      );
    }
  });

  onDestroy(() => {
    if (pollInterval) clearInterval(pollInterval);
  });
</script>

<div>
  <!-- ── WELCOME SECTION ──────────────────────────────────────── -->
  <section id="welcome" class="mb-8">
    <div
      class="animate-fade-in"
    >
      <div
        class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-commissioner) mb-2"
      >
        Front Office
      </div>
      <h1
        class="font-display text-3xl md:text-4xl font-extrabold tracking-tight text-(--color-text) leading-tight"
      >
        {#if user}
          Welcome,<br /><span class="text-(--color-accent-ember)">{user.display_name}</span>
        {:else}
          Command<br />Center
        {/if}
      </h1>
      <p
        class="mt-2 font-mono text-xs text-(--color-text-muted) tracking-wide"
      >
        // SABERMETRICS &amp; TEAM OPERATIONS
      </p>
    </div>
  </section>

  {#if loading}
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
      {#each [1, 2, 3] as _}
        <div class="card p-6">
          <div class="skeleton h-4 w-24 mb-3"></div>
          <div class="skeleton h-8 w-16"></div>
        </div>
      {/each}
    </div>
  {:else if error}
    <div class="card p-6 border-l-4 border-l-(--color-danger)">
      <p class="font-mono text-sm font-bold text-(--color-danger) mb-2">{error}</p>
      <p class="text-sm text-(--color-text-muted)">
        Make sure the league has been synced. Check the admin panel.
      </p>
    </div>
  {:else}
    <!-- Stats overview row -->
    <div
      class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8 stagger-in"
    >
      <!-- Active League tile — left accent -->
      <div
        class="card p-5 border-l-4 border-l-(--color-commissioner) relative overflow-hidden"
      >
        <div
          class="absolute top-0 right-0 w-16 h-16 bg-(--color-commissioner-muted) opacity-50"
          style="clip-path: polygon(100% 0, 100% 100%, 0 0);"
        ></div>
        <div>
          <h3
            class="font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted) mb-1"
          >
            Active League
          </h3>
          <p
            class="font-display text-lg font-bold text-(--color-text) truncate"
          >
            {standings?.league_name || "N/A"}
          </p>
        </div>
        <p
          class="mt-3 font-mono text-xs text-(--color-text-muted)"
        >
          S{standings?.season} &middot; WK {standings?.current_week}
        </p>
      </div>

      <!-- Organizations tile — center, inverted -->
      <div
        class="card p-5 bg-(--color-surface-raised) flex flex-col justify-between"
      >
        <div>
          <h3
            class="font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted) mb-1"
          >
            Organizations
          </h3>
          <p
            class="font-display text-3xl font-extrabold text-(--color-text)"
          >
            {standings?.standings?.length || 0}
          </p>
        </div>
        <a
          href="#standings"
          class="mt-3 flex items-center justify-between font-mono text-xs font-bold tracking-wide text-(--color-commissioner) hover:text-(--color-accent-ember) group"
        >
          <span>Standings Engine</span>
          <span
            class="group-hover:translate-y-0.5 transition-transform"
            >&darr;</span
          >
        </a>
      </div>

      <!-- Quick Links tile — right -->
      <div class="card p-5">
        <h3
          class="font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted) mb-3"
        >
          Quick Links
        </h3>
        <nav class="flex flex-col gap-1">
          <a
            href="#matchup"
            class="flex items-center justify-between py-1.5 px-2 rounded-sm font-mono text-xs text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised) group"
          >
            <span>My Matchup</span>
            <span class="text-(--color-text-muted) group-hover:translate-x-0.5 transition-transform"
              >&rarr;</span
            >
          </a>
          <a
            href="#league-matchups"
            class="flex items-center justify-between py-1.5 px-2 rounded-sm font-mono text-xs text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised) group"
          >
            <span>League Matchups</span>
            <span class="text-(--color-text-muted) group-hover:translate-x-0.5 transition-transform"
              >&rarr;</span
            >
          </a>
          <a
            href="/bench"
            class="flex items-center justify-between py-1.5 px-2 rounded-sm font-mono text-xs text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised) group"
          >
            <span>Manage Bench</span>
            <span class="text-(--color-text-muted) group-hover:translate-x-0.5 transition-transform"
              >&rarr;</span
            >
          </a>
          <a
            href="/commish-notes"
            class="flex items-center justify-between py-1.5 px-2 rounded-sm font-mono text-xs text-(--color-text-secondary) hover:text-(--color-text) hover:bg-(--color-surface-raised) group"
          >
            <span>Commish Notes</span>
            <span class="text-(--color-text-muted) group-hover:translate-x-0.5 transition-transform"
              >&rarr;</span
            >
          </a>
        </nav>
      </div>
    </div>

    {#if matchups?.matchups?.length}
      {#if myMatchup}
        <section id="matchup" class="mb-10">
          <div
            class="flex items-center justify-between mb-4"
          >
            <div class="flex items-center gap-3">
              <div class="w-1 h-6 bg-(--color-commissioner) rounded-full"></div>
              <h2
                class="font-display text-xl md:text-2xl font-extrabold tracking-tight text-(--color-text)"
              >
                Week {matchups.week} — My Matchup
              </h2>
            </div>
            {#if isGameHours()}
              <span
                class="badge badge-success"
              >
                <span class="status-dot status-dot-success"
                ></span>
                Live Sync Active
              </span>
            {/if}
          </div>

          <div class="card overflow-hidden">
            <div class="flex">
              <div
                class="relative flex flex-1 flex-col items-center justify-center p-4 sm:p-6 {myMatchup.team_a_wins >
                myMatchup.team_b_wins
                  ? 'bg-(--color-success-muted)'
                  : ''}"
              >
                {#if myMatchup.team_a_wins > myMatchup.team_b_wins}
                  <div
                    class="absolute top-0 left-0 right-0 h-0.5 bg-(--color-success)"
                  ></div>
                {/if}
                <div class="flex flex-col items-center gap-2 mb-2">
                  {#if myMatchup.team_a_logo}
                    <img
                      src={myMatchup.team_a_logo}
                      alt=""
                      class="h-10 w-10 rounded-sm"
                    />
                  {/if}
                  <p
                    class="font-display text-sm font-bold text-(--color-text) text-center"
                  >
                    {myMatchup.team_a_name}
                  </p>
                  <p
                    class="font-mono text-[0.6rem] text-(--color-text-muted)"
                  >
                    {getTeamRecord(myMatchup.team_a_name) || "—"} record
                  </p>
                </div>
                <p
                  class="mt-2 font-mono text-5xl sm:text-6xl font-black {myMatchup.team_a_wins >
                  myMatchup.team_b_wins
                    ? 'text-(--color-success)'
                    : 'text-(--color-text)'}"
                >
                  {myMatchup.team_a_wins}
                </p>
              </div>

              <div
                class="flex items-center justify-center px-3 bg-(--color-surface-inset)"
              >
                <span
                  class="font-mono text-xs font-bold tracking-widest text-(--color-text-muted) uppercase"
                  >VERSUS</span
                >
              </div>

              <div
                class="relative flex flex-1 flex-col items-center justify-center p-4 sm:p-6 {myMatchup.team_b_wins >
                myMatchup.team_a_wins
                  ? 'bg-(--color-success-muted)'
                  : ''}"
              >
                {#if myMatchup.team_b_wins > myMatchup.team_a_wins}
                  <div
                    class="absolute top-0 left-0 right-0 h-0.5 bg-(--color-success)"
                  ></div>
                {/if}
                <div class="flex flex-col items-center gap-2 mb-2">
                  {#if myMatchup.team_b_logo}
                    <img
                      src={myMatchup.team_b_logo}
                      alt=""
                      class="h-10 w-10 rounded-sm"
                    />
                  {/if}
                  <p
                    class="font-display text-sm font-bold text-(--color-text) text-center"
                  >
                    {myMatchup.team_b_name}
                  </p>
                  <p
                    class="font-mono text-[0.6rem] text-(--color-text-muted)"
                  >
                    {getTeamRecord(myMatchup.team_b_name) || "—"} record
                  </p>
                </div>
                <p
                  class="mt-2 font-mono text-5xl sm:text-6xl font-black {myMatchup.team_b_wins >
                  myMatchup.team_a_wins
                    ? 'text-(--color-success)'
                    : 'text-(--color-text)'}"
                >
                  {myMatchup.team_b_wins}
                </p>
              </div>
            </div>

            <div
              class="flex items-center justify-center gap-3 py-2 border-t border-(--color-border-subtle) bg-(--color-surface-inset)"
            >
              {#if myMatchup.is_complete}
                <span
                  class="badge badge-info"
                  >FINAL</span
                >
              {:else if isPreseason}
                <span
                  class="badge badge-warning"
                  >PRESEASON</span
                >
              {:else}
                <span
                  class="badge badge-success"
                  >LIVE</span
                >
              {/if}
              {#if myMatchup.ties > 0}
                <span class="badge"
                  >{myMatchup.ties} TIES</span
                >
              {/if}
            </div>

            <div class="p-4 sm:p-6 border-t border-(--color-border)">
              <div
                class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4"
              >
                <div>
                  <h3
                    class="font-display text-base font-bold text-(--color-text)"
                  >
                    Category Breakdown
                  </h3>
                  <p
                    class="text-xs text-(--color-text-muted) mt-0.5"
                  >
                    {#if isPreseason}
                      Matchups unlock once the season week ({startWeek}) begins.
                      No live head-to-head stats yet.
                    {:else}
                      Scores refresh automatically during active game windows.
                    {/if}
                  </p>
                </div>
                {#if hasMatchupStats}
                  <span
                    class="badge"
                    >Week {matchups?.week || currentWeek}</span
                  >
                {/if}
              </div>

              {#if !myMatchup}
                <div
                  class="rounded-sm bg-(--color-surface-inset) p-4 text-sm text-(--color-text-muted) text-center"
                >
                  No matchup found for your club this week.
                </div>
              {:else if isPreseason}
                <div
                  class="rounded-sm bg-(--color-surface-inset) p-4 text-sm text-(--color-text-muted) text-center"
                >
                  Preseason mode: once Yahoo posts the first box scores, we’ll
                  populate every scoring category here automatically.
                </div>
              {:else if hasMatchupStats}
                <div class="overflow-x-auto -mx-4 sm:-mx-6">
                  <table
                    class="w-full text-sm"
                  >
                    <thead>
                      <tr
                        class="border-b border-(--color-border)"
                      >
                        <th class="px-4 sm:px-6 py-3 text-left font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted)">Team</th>
                        {#each categoryNames as cat}
                          <th class="px-3 py-3 text-center font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted)">{cat}</th>
                        {/each}
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-(--color-border-subtle)">
                      {#each teamRows as team}
                        <tr class="hover:bg-(--color-surface-raised)">
                          <td class="px-4 sm:px-6 py-3">
                            <div class="flex items-center gap-2.5">
                              {#if team.logo}
                                <img
                                  src={team.logo}
                                  alt=""
                                  class="h-7 w-7 rounded-sm"
                                />
                              {/if}
                              <div>
                                <p
                                  class="font-display text-sm font-bold text-(--color-text)"
                                >
                                  {team.name}
                                </p>
                                {#if getTeamRecord(team.name)}
                                  <p
                                    class="font-mono text-[0.6rem] text-(--color-text-muted)"
                                  >
                                    {getTeamRecord(team.name)} record
                                  </p>
                                {/if}
                              </div>
                            </div>
                          </td>
                          {#each categoryNames as cat}
                            <td
                              class="px-3 py-4 text-center font-mono text-sm {categoryWinner(
                                cat,
                                team.key,
                              )
                                ? 'text-success font-bold'
                                : 'text-(--color-text-muted)'}"
                            >
                              {formatStatValue(team.stats, cat)}
                            </td>
                          {/each}
                        </tr>
                      {/each}
                    </tbody>
                  </table>
                </div>
              {:else}
                <div
                  class="rounded-sm bg-(--color-surface-inset) p-4 text-sm text-(--color-text-muted) text-center"
                >
                  Waiting on Yahoo scorekeepers. Stats will populate once the
                  first games in this matchup are underway.
                </div>
              {/if}
            </div>
          </div>
        </section>
      {/if}

      <!-- ── LEAGUE MATCHUPS SECTION ──────────────────────────────── -->
      <section id="league-matchups" class="mb-10">
        <div
          class="flex items-center gap-3 mb-4"
        >
          <div class="w-1 h-6 bg-(--color-commissioner) rounded-full"></div>
          <h2
            class="font-display text-xl md:text-2xl font-extrabold tracking-tight text-(--color-text)"
          >
            Week {matchups.week} — League Matchups
          </h2>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          {#each matchups.matchups as matchup}
            <div
              class="card overflow-hidden"
            >
              <div class="flex">
                <div
                  class="relative flex flex-1 flex-col items-center justify-center p-3 {matchup.team_a_wins >
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
                    class="mt-1 font-mono text-3xl font-black {matchup.team_a_wins >
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
                    >VS</span
                  >
                </div>

                <div
                  class="relative flex flex-1 flex-col items-center justify-center p-3 {matchup.team_b_wins >
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
                    class="mt-1 font-mono text-3xl font-black {matchup.team_b_wins >
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
                {:else if isPreseason}
                  <span
                    class="badge badge-warning"
                    >PRESEASON</span
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
      </section>
    {/if}

    <!-- ── STANDINGS ENGINE SECTION ─────────────────────────────── -->
    {#if standings?.standings?.length}
      <section id="standings" class="mb-10">
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center gap-3">
            <div class="w-1 h-6 bg-(--color-commissioner) rounded-full"></div>
            <div>
              <span
                class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
                >Season {standings.season}</span
              >
              <h2
                class="font-display text-xl md:text-2xl font-extrabold tracking-tight text-(--color-text)"
              >
                Standings Engine
              </h2>
            </div>
          </div>
          {#if standings.current_week}
            <span
              class="badge"
            >
              WK {standings.current_week}
            </span>
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
                <th class="px-3 py-3 text-center font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted) hidden sm:table-cell"
                  >Win %</th
                >
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
                          {(team.team_name || "?").charAt(0)}
                        </div>
                      {/if}
                      <span
                        class="font-display text-sm font-bold text-(--color-text)"
                      >
                        {team.team_name}
                      </span>
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
                      : 'text-(--color-text-muted)'}"
                  >
                    {winPct.replace(/^0+/, "")}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </section>
    {/if}
  {/if}
</div>
