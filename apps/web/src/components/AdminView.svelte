<!--
  Admin View - Commissioner dashboard for league management

  Provides overview of system status, job management, notifications,
  and administrative actions. Supports manual job triggering, stopping,
  and configuration management. Requires commissioner role.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { fetchUser, getUser } from "../lib/stores.svelte";
  import { navigate } from "astro:transitions/client";

  let {
    section = "overview",
  }: { section?: "overview" | "settings" | "recaps" | "mappings" } = $props();

  let user = $derived(getUser());
  let overview: any = $state(null);
  let notifications: any[] = $state([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let syncStatus = $state<string | null>(null);
  let syncingJob = $state<string | null>(null);
  let jobStopAction = $state<"stop" | "resume" | null>(null);
  const warmupFacts = [
    "Calibrating matchup differentials and Elo drift...",
    "Handshaking with Yahoo Fantasy API · awaiting commissioner auth",
    "Priming Redis telemetry buffers for job tracking...",
    "Hydrating MLB player mappings for cross-checks...",
  ];
  const warmupSignals = [
    {
      label: "OAuth Relay",
      status: "Awaiting commissioner login",
      accent: "text-commissioner",
    },
    {
      label: "Redis Telemetry",
      status: "Job channels syncing",
      accent: "text-success",
    },
    {
      label: "Worker Cluster",
      status: "Spinning up cron deck",
      accent: "text-commissioner",
    },
    {
      label: "Data Vault",
      status: "Encrypting secrets",
      accent: "text-(--color-text-muted)",
    },
  ];
  let warmupFactIndex = $state(0);

  let clearedPage = $state(0);
  const CLEARED_PAGE_SIZE = 10;

  const syncGroups = [
    {
      title: "PRESEASON INITIALIZATION",
      description:
        "Automated tasks to prepare the league for a new season. Start here.",
      columns: "sm:grid-cols-2 lg:grid-cols-4",
      jobs: [
        {
          name: "run_preseason_setup_job",
          label: "Run Setup Orchestrator (Safe)",
        },
        { name: "sync_league_meta", label: "1. Sync League Rules" },
        { name: "sync_matchups", label: "2. Sync Initial Matchups" },
        { name: "resolve_player_mappings", label: "3. Resolve Player IDs" },
      ],
    },
    {
      title: "IN-SEASON MAINTENANCE (DAILY ROUTINE)",
      description:
        "Automated sequence to pull the morning updates and run fresh numbers.",
      columns: "grid-cols-1",
      jobs: [
        {
          name: "run_daily_sync_job",
          label: "RUN DAILY SYNC ORCHESTRATOR",
          subtext:
            "Runs a complete sequence: Rosters → Free Agents → Salaries/Rules → MLB Injuries → Season & Next(7) Values → Briefings.",
        },
      ],
    },
    {
      title: "INDIVIDUAL API SYNC JOBS",
      description:
        "Granular control over specific external APIs and internal recomputations.",
      columns: "sm:grid-cols-2 lg:grid-cols-3",
      jobs: [
        { name: "sync_roster", label: "01. Sync Team Rosters" },
        { name: "sync_free_agents", label: "02. Sync Free Agents" },
        { name: "sync_roster_trends", label: "03. Sync Roster Trends" },
        { name: "sync_rotowire_injuries", label: "04. Sync RotoWire Injuries" },
        { name: "sync_injury_status", label: "05. Sync MLB Injuries" },
        {
          name: "sync_advanced_metrics_job",
          label: "06. Sync Advanced Metrics",
        },
        {
          name: "recompute_season_values",
          label: "07. Recompute Season Values",
        },
        {
          name: "recompute_next_games_values",
          label: "08. Recompute Next-Games Values",
        },
        { name: "generate_briefings", label: "09. Generate Morning Briefings" },
        {
          name: "generate_weekly_recaps",
          label: "10. Generate AI Weekly Reports",
        },
      ],
    },
    {
      title: "DANGER ZONE (OVERRIDES)",
      description:
        "Manual overrides and complete data resets. Use with caution.",
      columns: "sm:grid-cols-2 lg:grid-cols-3",
      jobs: [
        { name: "load_mlb_roster_data", label: "Force Load MLB API" },
        { name: "load_fangraphs_stats", label: "Force Load FanGraphs Stats" },
        {
          name: "run_force_preseason_setup_job",
          label: "FLUSH APP & RESTART SETUP",
        },
      ],
    },
  ];

  let activeNotifications = $derived(
    notifications.filter((n: any) => !n.is_read),
  );
  let clearedNotifications = $derived(
    notifications.filter((n: any) => n.is_read),
  );
  let clearedTotalPages = $derived(
    Math.max(1, Math.ceil(clearedNotifications.length / CLEARED_PAGE_SIZE)),
  );
  let clearedPageItems = $derived(
    clearedNotifications.slice(
      clearedPage * CLEARED_PAGE_SIZE,
      (clearedPage + 1) * CLEARED_PAGE_SIZE,
    ),
  );

  async function loadData() {
    try {
      const [overviewData, notifResponse] = await Promise.all([
        api.get("/admin/overview"),
        api.get<any>("/admin/notifications"),
      ]);
      overview = overviewData;
      notifications = notifResponse.notifications || [];
      error = null;
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  function formatDateTime(iso: string | null | undefined): string {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return "";
    }
  }

  onMount(() => {
    let overviewTimer: ReturnType<typeof setInterval> | null = null;
    let warmupTimer: ReturnType<typeof setInterval> | null = null;

    const init = async () => {
      await fetchUser();
      const u = getUser();
      if (!u || u.role !== "commissioner") {
        navigate("/dashboard", { history: "replace" });
        return;
      }
      await loadData();
      overviewTimer = setInterval(loadData, 5000);
      warmupTimer = setInterval(() => {
        warmupFactIndex = (warmupFactIndex + 1) % warmupFacts.length;
      }, 3500);
    };

    init();

    return () => {
      if (overviewTimer) clearInterval(overviewTimer);
      if (warmupTimer) clearInterval(warmupTimer);
    };
  });

  async function triggerSync(jobName: string) {
    syncStatus = `Triggering ${jobName}...`;
    syncingJob = jobName;
    try {
      await api.post("/admin/sync", { job_name: jobName });
      syncStatus = `${jobName} queued — worker is processing. Check status below.`;
      loadData();
      setTimeout(() => (syncStatus = null), 5000);
    } catch (e: any) {
      syncStatus = `Failed to queue: ${e.message}`;
    } finally {
      syncingJob = null;
    }
  }

  async function refreshJobStatus() {
    syncStatus = "Refreshing job status cache...";
    try {
      const response = await api.post<{ refreshed_jobs?: string[] }>(
        "/admin/refresh-job-status",
      );
      const refreshed = response?.refreshed_jobs ?? [];
      if (refreshed.length > 0) {
        syncStatus = `Refreshed: ${refreshed.join(", ")}`;
      } else {
        syncStatus = "No stuck jobs found - all statuses up to date";
      }
      loadData(); // Reload the overview to show updated statuses
      setTimeout(() => (syncStatus = null), 3000);
    } catch (e: any) {
      syncStatus = `Failed to refresh: ${e.message}`;
      setTimeout(() => (syncStatus = null), 3000);
    }
  }

  async function issueJobStop(action: "stop" | "resume") {
    jobStopAction = action;
    const isStop = action === "stop";
    syncStatus = isStop ? "Issuing HARD STOP..." : "Clearing HARD STOP flag...";
    try {
      const response = await api.post<{ since?: string }>(
        isStop ? "/admin/jobs/stop" : "/admin/jobs/resume",
      );
      if (isStop) {
        const since = response?.since
          ? new Date(response.since).toLocaleTimeString()
          : "now";
        syncStatus = `HARD STOP active (${since})`;
      } else {
        syncStatus = "Workers resumed — queues can run again.";
      }
      await loadData();
      setTimeout(() => (syncStatus = null), 4000);
    } catch (e: any) {
      syncStatus = `Failed to ${isStop ? "stop" : "resume"}: ${e.message}`;
      setTimeout(() => (syncStatus = null), 4000);
    } finally {
      jobStopAction = null;
    }
  }

  async function markRead(id: number) {
    try {
      await api.post(`/admin/notifications/${id}/read`);
      notifications = notifications.map((n: any) =>
        n.id === id ? { ...n, is_read: true } : n,
      );
    } catch {
      /* silent */
    }
  }

  async function markAllRead() {
    try {
      await api.post("/admin/notifications/read-all");
      notifications = notifications.map((n: any) => ({
        ...n,
        is_read: true,
      }));
    } catch {
      /* silent */
    }
  }

  function formatElapsed(seconds: number | null | undefined): string {
    if (seconds == null) return "";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600)
      return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }

  function formatTime(iso: string | null | undefined): string {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return "";
    }
  }

  function jobStatusLabel(
    statusData: any,
    isSyncing: boolean,
  ): { text: string; color: string } {
    if (isSyncing) return { text: "QUEUING...", color: "text-commissioner" };
    if (!statusData || statusData.status === "idle")
      return { text: "-> INIT", color: "opacity-50" };
    if (statusData.status === "deferred") {
      return {
        text: "DEFERRED — awaiting bootstrap",
        color: "text-warning",
      };
    }
    if (statusData.status === "processing") {
      const elapsed = formatElapsed(statusData.elapsed_seconds);
      return {
        text: `PROCESSING ${elapsed ? `(${elapsed})` : "..."}`,
        color: "text-commissioner",
      };
    }
    if (statusData.status === "success") {
      const ts = formatTime(statusData.completed_at);
      const elapsed = formatElapsed(statusData.elapsed_seconds);
      return {
        text: `OK ${elapsed ? `in ${elapsed}` : ""} ${ts ? `@ ${ts}` : ""}`,
        color: "text-success",
      };
    }
    if (statusData.status === "failed") {
      const ts = formatTime(statusData.completed_at);
      return {
        text: `FAILED ${ts ? `@ ${ts}` : ""}`,
        color: "text-danger",
      };
    }
    return { text: "UNKNOWN", color: "text-(--color-text-muted)" };
  }
</script>

{#if loading}
  <section
    class="flex flex-col items-center justify-center min-h-[60vh] text-center px-4"
  >
    <div class="relative w-full max-w-md">
      <div
        class="hidden"
      ></div>
      <div
        class="hidden"
      ></div>
      <div
        class="hidden"
        style="animation-duration: 18s"
      ></div>
    </div>
    <div class="w-full max-w-md">
      <div class="mb-6">
        <p
          class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-commissioner) mb-2"
        >
          Sabermetric Platform
        </p>
        <h1
          class="font-display text-3xl font-extrabold tracking-tight text-(--color-text)"
        >
          Warming Up
        </h1>
        <p
          class="font-mono text-[0.6rem] text-(--color-text-muted) mt-2"
        >
          Queuing workers · Waiting for commissioner OAuth handshake · Syncing
          datapipes
        </p>
      </div>

      <div
        class="h-1 w-full rounded-full bg-(--color-surface-inset) overflow-hidden mb-6"
      >
        <div
          class="h-full bg-(--color-commissioner) rounded-full transition-all duration-700"
          style={`width: ${40 + warmupFactIndex * 15}%`}
        ></div>
      </div>

      <div class="grid grid-cols-2 gap-3 mb-6">
        {#each warmupSignals as signal}
          <div
            class="rounded-sm bg-(--color-surface-raised) border border-(--color-border-subtle) p-3 text-left"
          >
            <p
              class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
            >
              {signal.label}
            </p>
            <p class="font-mono text-[0.55rem] {signal.accent} mt-0.5">
              {signal.status}
            </p>
          </div>
        {/each}
      </div>

      <div
        class="flex items-center justify-center gap-2 font-mono text-[0.6rem] text-(--color-text-muted)"
      >
        <span class="flex items-center">
          <span
            class="inline-block h-2 w-2 rounded-full bg-(--color-commissioner) animate-pulse mr-1.5"
          ></span>
        </span>
        <span>// {warmupFacts[warmupFactIndex]}</span>
      </div>
    </div>
  </section>
{:else if error}
  <div class="card p-6 border-l-4 border-l-(--color-danger) font-mono text-sm text-(--color-danger)">{error}</div>
{:else}
  <div class="space-y-8">
    <div
      class="grid grid-cols-2 md:grid-cols-4 gap-3"
    >
      <div
        class="card p-4"
      >
        <div
          class="font-mono text-[0.55rem] font-bold tracking-widest text-(--color-text-muted) mb-1"
        >
          01
        </div>
        <p
          class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
        >
          REGISTERED USERS
        </p>
        <p
          class="font-display text-2xl font-extrabold text-(--color-text) mt-1"
        >
          {overview.user_count ?? "-"}
        </p>
      </div>
      <div
        class="card p-4"
      >
        <div
          class="font-mono text-[0.55rem] font-bold tracking-widest text-(--color-text-muted) mb-1"
        >
          02
        </div>
        <p
          class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
        >
          ACTIVE FRANCHISES
        </p>
        <p
          class="font-display text-2xl font-extrabold text-(--color-text) mt-1"
        >
          {overview.team_count ?? "-"}
        </p>
      </div>
      <div
        class="card p-4"
      >
        <div
          class="font-mono text-[0.55rem] font-bold tracking-widest text-(--color-text-muted) mb-1"
        >
          03
        </div>
        <p
          class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
        >
          PLAYER IDENTITIES
        </p>
        <p
          class="font-display text-2xl font-extrabold text-(--color-text) mt-1"
        >
          {overview.player_count ?? "-"}
        </p>
      </div>
      <div
        class="card p-4"
      >
        <div
          class="font-mono text-[0.55rem] font-bold tracking-widest text-(--color-text-muted) mb-1"
        >
          04
        </div>
        <p
          class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
        >
          FRONT OFFICE REPORTS
        </p>
        <p
          class="font-display text-2xl font-extrabold text-(--color-text) mt-1"
        >
          {overview.recap_count ?? "-"}
        </p>
      </div>
    </div>

    <div class="space-y-4">
      <div
        class="flex items-center gap-3"
      >
        <span
          class="font-mono text-[0.55rem] font-bold tracking-widest text-(--color-text-muted)"
          >01</span
        >
        <h2 class="font-display text-xl font-extrabold tracking-tight text-(--color-text)">
          DATA SYNCHRONIZATION
        </h2>
      </div>

      {#if syncStatus}
        <div
          class="rounded-sm bg-(--color-surface-inset) border border-(--color-border-subtle) px-4 py-2 font-mono text-xs text-(--color-commissioner)"
        >
          [SYS] {syncStatus}
        </div>
      {/if}

      <!-- Job Controls -->
      <div
        class="space-y-3"
      >
        <div
          class={`border-2 px-4 py-3 font-mono text-[10px] font-bold uppercase tracking-[0.35em] ${
            overview?.job_stop_active
              ? "border-(--color-danger) bg-(--color-danger-muted) text-(--color-danger)"
              : "border-(--color-success) bg-(--color-success-muted) text-(--color-success)"
          }`}
        >
          {#if overview?.job_stop_active}
            HARD STOP ACTIVE · since {formatDateTime(overview.job_stop_since)}
          {:else}
            Workers listening — no HARD STOP flag detected
          {/if}
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            onclick={refreshJobStatus}
            class="btn btn-secondary text-xs"
          >
            REFRESH JOB STATUS
          </button>
          <button
            onclick={() => issueJobStop("stop")}
            disabled={overview?.job_stop_active || jobStopAction !== null}
            class="btn btn-danger text-xs"
          >
            ISSUE HARD STOP
          </button>
          <button
            onclick={() => issueJobStop("resume")}
            disabled={!overview?.job_stop_active || jobStopAction !== null}
            class="btn btn-success text-xs"
          >
            RESUME WORKERS
          </button>
        </div>
      </div>
      <div class="space-y-6">
        {#each syncGroups as group}
          <div>
            <div class="mb-3">
              <h3
                class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text-muted)"
              >
                {group.title}
              </h3>
              <p
                class="font-mono text-[0.55rem] text-(--color-text-muted) mt-0.5"
              >
                // {group.description}
              </p>
            </div>
            <div
              class="grid gap-2 {group.columns}"
            >
              {#each group.jobs as job}
                {@const statusData = overview?.job_statuses?.find(
                  (s: any) => s.job_name === job.name,
                )}
                {@const isSyncing = syncingJob === job.name}
                {@const isProcessing =
                  statusData?.status === "processing" || isSyncing}
                {@const isDanger = group.title.includes("DANGER")}
                {@const statusInfo = jobStatusLabel(statusData, isSyncing)}
                <button
                  onclick={() => triggerSync(job.name)}
                  disabled={isProcessing}
                  class="group flex flex-col items-start justify-between bg-(--color-surface) px-4 py-4 font-mono text-[10px] font-black uppercase tracking-widest transition hover:bg-(--color-surface-raised) border-l-4 {isDanger
                    ? 'border-(--color-danger) hover:border-(--color-danger) text-(--color-text)'
                    : 'border-(--color-border-heavy) opacity-50 hover:border-(--color-accent-amber) text-(--color-text)'} {isProcessing
                    ? 'bg-(--color-surface-base) cursor-not-allowed opacity-80'
                    : ''}"
                >
                  <div class="flex-1">
                    <div
                      class={group.columns.includes("grid-cols-1")
                        ? "text-xl md:text-2xl mt-4 mb-2 font-black text-(--color-text) group-hover:text-black"
                        : ""}
                    >
                      {job.label}
                    </div>
                    {#if "subtext" in job && job.subtext}
                      <div
                        class="mt-1 text-[8px] tracking-[0.2em] opacity-60 normal-case"
                      >
                        // {job.subtext}
                      </div>
                    {/if}
                  </div>
                  <div
                    class="w-full text-right mt-auto {statusInfo.color} {isProcessing
                      ? 'blink'
                      : ''}"
                  >
                    {statusInfo.text}
                  </div>
                  {#if statusData?.status === "failed" && statusData?.error}
                    <div
                      class="w-full text-[8px] tracking-[0.15em] text-danger mt-1 truncate"
                      title={statusData.error}
                    >
                      {statusData.error}
                    </div>
                  {:else if statusData?.status === "deferred" && statusData?.error}
                    <div
                      class="w-full text-[8px] tracking-[0.15em] text-warning mt-1 truncate"
                      title={statusData.error}
                    >
                      {statusData.error}
                    </div>
                  {/if}
                </button>
              {/each}
            </div>
          </div>
        {/each}
      </div>
    </div>

    <div class="space-y-4">
      <div
        class="flex items-center justify-between"
      >
        <div class="flex items-center gap-3">
          <span
            class="font-mono text-[0.55rem] font-bold tracking-widest text-(--color-text-muted)"
            >02</span
          >
          <h2 class="font-display text-xl font-extrabold tracking-tight text-(--color-text)">
            ACTIVE ALERTS
          </h2>
          {#if activeNotifications.length > 0}
            <span
              class="badge badge-danger"
            >
              {activeNotifications.length} UNREAD
            </span>
          {/if}
        </div>
        {#if activeNotifications.length > 0}
          <button
            onclick={markAllRead}
            class="btn btn-secondary text-xs"
          >
            CLEAR ALL
          </button>
        {/if}
      </div>

      {#if activeNotifications.length === 0}
        <div
          class="card p-6 text-center"
        >
          <p
            class="font-mono text-xs text-(--color-text-muted)"
          >
            // ZERO UNREAD INTERRUPTS //
          </p>
        </div>
      {:else}
        <div class="space-y-2">
          {#each activeNotifications as notif}
            <div
              class="card p-4 flex flex-col sm:flex-row sm:items-start gap-3"
            >
              <div class="flex-1 min-w-0">
                <div class="flex flex-wrap items-center gap-2 mb-1">
                  <span
                    class="font-mono text-[10px] font-black uppercase tracking-widest px-2 py-0.5 border {notif.type ===
                    'ai_failure'
                      ? 'badge-danger'
                      : notif.type === 'sync_failure'
                        ? 'badge-warning'
                        : notif.type === 'job_deferred'
                          ? 'badge-warning'
                          : 'border-primary bg-secondary text-tertiary'}"
                  >
                    {notif.type}
                  </span>
                  <span
                    class="font-mono text-[0.55rem] text-(--color-text-muted)"
                  >
                    <span class="hidden"
                    ></span>
                    {new Date(notif.created_at).toLocaleString()}
                  </span>
                </div>
                <p
                  class="font-mono text-xs text-(--color-text-muted)"
                >
                  > {notif.message}
                </p>
              </div>
              <button
                onclick={() => markRead(notif.id)}
                class="btn btn-secondary text-xs shrink-0"
              >
                ACKNOWLEDGE
              </button>
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <div class="space-y-4">
      <div
        class="flex items-center justify-between"
      >
        <div class="flex items-center gap-3">
          <span
            class="font-mono text-[0.55rem] font-bold tracking-widest text-(--color-text-muted)"
            >03</span
          >
          <h2 class="font-display text-xl font-extrabold tracking-tight text-(--color-text)">
            CLEARED LOG
          </h2>
          {#if clearedNotifications.length > 0}
            <span
              class="badge"
            >
              {clearedNotifications.length} TOTAL
            </span>
          {/if}
        </div>
        {#if clearedTotalPages > 1}
          <div class="flex items-center gap-2">
            <button
              onclick={() => (clearedPage = Math.max(0, clearedPage - 1))}
              disabled={clearedPage === 0}
              class="btn btn-secondary px-3 py-1 text-xs"
            >
              PREV
            </button>
            <span
              class="font-mono text-xs font-bold text-(--color-text-muted) px-2"
            >
              {clearedPage + 1} / {clearedTotalPages}
            </span>
            <button
              onclick={() =>
                (clearedPage = Math.min(
                  clearedTotalPages - 1,
                  clearedPage + 1,
                ))}
              disabled={clearedPage >= clearedTotalPages - 1}
              class="btn btn-secondary px-3 py-1 text-xs"
            >
              NEXT
            </button>
          </div>
        {/if}
      </div>

      {#if clearedNotifications.length === 0}
        <div
          class="card p-6 text-center"
        >
          <p
            class="font-mono text-xs text-(--color-text-muted)"
          >
            // NO CLEARED ENTRIES //
          </p>
        </div>
      {:else}
        <div
          class="space-y-2"
        >
          {#each clearedPageItems as notif}
            <div
              class="card p-4 opacity-60"
            >
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2 mb-1">
                  <span
                    class="font-mono text-[10px] font-black uppercase tracking-widest px-2 py-0.5 border opacity-60 {notif.type ===
                    'ai_failure'
                      ? 'border-(--color-danger) bg-(--color-danger-muted) text-(--color-danger)'
                      : notif.type === 'sync_failure'
                        ? 'border-(--color-warning) bg-(--color-warning-muted) text-(--color-warning)'
                        : notif.type === 'job_deferred'
                          ? 'border-(--color-warning) bg-(--color-warning-muted) text-(--color-warning)'
                          : 'border-(--color-border) bg-(--color-surface-raised) text-(--color-text-muted)'}"
                  >
                    {notif.type}
                  </span>
                  <span
                    class="font-mono text-[0.55rem] text-(--color-text-muted)"
                  >
                    {new Date(notif.created_at).toLocaleString()}
                  </span>
                </div>
                <p
                  class="font-mono text-xs text-(--color-text-muted)"
                >
                  > {notif.message}
                </p>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
{/if}
