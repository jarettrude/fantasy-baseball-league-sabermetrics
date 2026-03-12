<!--
  Admin Recaps View - Recap management

  Admin interface for managing AI-generated recaps including
  viewing, editing, and deleting recap content.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";

  interface Recap {
    id: number;
    week: number;
    type: string;
    team_id: number | null;
    team_name: string | null;
    status: string;
    content: string | null;
    model_used: string | null;
    tokens_used: number | null;
    cost_usd: string | null;
    published_at: string | null;
    created_at: string;
  }

  let selectedWeek = $state(1);
  let maxWeek = $state(1);
  let recaps = $state<Recap[]>([]);
  let loading = $state(false);
  let editingRecap = $state<Recap | null>(null);
  let editContent = $state("");
  let saving = $state(false);
  let regeneratingId = $state<number | null>(null);
  let error = $state<string | null>(null);
  let successMessage = $state<string | null>(null);

  let history = $state<number[]>([]);

  /**
   * Fetches the unique list of weeks that have published recaps and current season context.
   */
  async function loadHistory() {
    try {
      const [historyData, league]: [number[], any] = await Promise.all([
        api.get<number[]>("/recaps/history"),
        api.get("/league/info"),
      ]);

      history = historyData;
      // Show at least up to the current week, but include future weeks if mock data exists
      const historicalMax = history.length > 0 ? Math.max(...history) : 0;
      maxWeek = Math.max(league.current_week || 1, historicalMax);

      if (history.length > 0 && selectedWeek === 1) {
        selectedWeek = history[0];
      } else {
        selectedWeek = maxWeek;
      }
    } catch {
      // fallback
    }
  }

  /**
   * Fetches the recap generations for the currently active selected week.
   */
  async function loadRecaps(weekOverride?: number) {
    if (weekOverride !== undefined) {
      selectedWeek = weekOverride;
    }
    loading = true;
    error = null;
    try {
      recaps = await api.get<Recap[]>(`/admin/recaps/${selectedWeek}`);
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  /**
   * Submits the administrator's manual markdown edits for a recap back to the server.
   * Updates the localized state on success without requiring a full refetch.
   */
  async function saveEdit() {
    if (!editingRecap) return;
    saving = true;
    try {
      await api.put(`/recaps/${editingRecap.id}`, { content: editContent });
      const idx = recaps.findIndex((r) => r.id === editingRecap!.id);
      if (idx !== -1) recaps[idx] = { ...recaps[idx], content: editContent };
      editingRecap = null;
      successMessage = "Recap saved";
      setTimeout(() => (successMessage = null), 3000);
    } catch (e: any) {
      error = e.message;
    } finally {
      saving = false;
    }
  }

  /**
   * Forces a regeneration of an individual AI recap using the LLM APIs asynchronously.
   * @param {number} recapId - ID of the target recap to regenerate
   */
  async function regenerate(recapId: number) {
    regeneratingId = recapId;
    error = null;
    try {
      await api.post(`/admin/recaps/${recapId}/regenerate`);
      successMessage = "Regeneration queued — refresh in a moment";
      setTimeout(() => (successMessage = null), 5000);
      await loadRecaps();
    } catch (e: any) {
      error = e.message;
    } finally {
      regeneratingId = null;
    }
  }

  /**
   * Opens the full-screen markdown editor modal for a specific recap.
   * @param {Recap} recap - The targeted recap object
   */
  function startEdit(recap: Recap) {
    editingRecap = recap;
    editContent = recap.content ?? "";
  }

  /** Closes the markdown editor modal and flushes state. */
  function cancelEdit() {
    editingRecap = null;
    editContent = "";
  }

  /**
   * Calculates structural Tailwind classes based on generation pipeline status.
   * @param {string} s - Generation status string
   * @returns {string} Tailwind classes
   */
  function statusBadgeClass(s: string) {
    if (s === "published") return "badge-success";
    if (s === "failed") return "badge-danger";
    return "badge-warning";
  }

  onMount(async () => {
    await loadHistory();
    await loadRecaps();
  });
</script>

<div class="grid grid-cols-1 lg:grid-cols-[1fr_220px] gap-6">
  <!-- Main Content - Order 1 on mobile, 2 on desktop -->
  <main class="order-2 lg:order-1 min-w-0">
    {#if error}
      <div
        class="card p-4 mb-4 border-l-4 border-l-(--color-danger) font-mono text-xs text-(--color-danger)"
      >
        {error}
      </div>
    {/if}

    {#if successMessage}
      <div
        class="card p-4 mb-4 border-l-4 border-l-(--color-success) font-mono text-xs text-(--color-success)"
      >
        {successMessage}
      </div>
    {/if}

    {#if loading}
      <div class="space-y-4">
        {#each [1, 2] as _}
          <div class="card p-6">
            <div
              class="skeleton h-5 w-40 mb-3"
            ></div>
            <div
              class="skeleton h-20 w-full"
            ></div>
          </div>
        {/each}
      </div>
    {:else if recaps.length === 0}
      <div
        class="card p-8 text-center"
      >
        <p
          class="font-mono text-sm text-(--color-text-muted)"
        >
          // NO REPORTS DATA FOR WEEK {selectedWeek} //
        </p>
        <p
          class="font-mono text-[0.6rem] text-(--color-text-muted) mt-2"
        >
          Sync league data or check worker logs to verify generation pipeline.
        </p>
      </div>
    {:else}
      {@const leagueRecaps = recaps.filter((r) => r.type === "league")}
      {@const managerRecaps = recaps.filter((r) => r.type === "manager")}

      {#if leagueRecaps.length > 0}
        <div class="mb-6">
          <h3
            class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text-muted) mb-3"
          >
            LEAGUE WIDE REPORTS
          </h3>
          <div class="space-y-4">
            {#each leagueRecaps as recap (recap.id)}
              <div
                class="card overflow-hidden"
              >
                <div
                  class="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 p-4 border-b border-(--color-border-subtle)"
                >
                  <div class="min-w-0">
                    <div class="flex flex-wrap items-center gap-2 mb-1">
                      <span
                        class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text)"
                      >
                        {recap.type === "league"
                          ? "LEAGUE REPORT"
                          : `MANAGER: ${recap.team_name ?? "UNKNOWN"}`}
                      </span>
                      <span
                        class="badge {statusBadgeClass(recap.status)}"
                      >
                        {recap.status}
                      </span>
                    </div>
                    <div
                      class="font-mono text-[0.55rem] text-(--color-text-muted) flex flex-wrap gap-2"
                    >
                      <span>[TOKENS: {recap.tokens_used ?? 0}]</span>
                      {#if recap.cost_usd}<span>[COST: ${recap.cost_usd}]</span
                        >{/if}
                      {#if recap.published_at}<span
                          >[TS: {new Date(
                            recap.published_at,
                          ).toISOString()}]</span
                        >{/if}
                    </div>
                  </div>

                  <div class="flex gap-2 shrink-0">
                    <button
                      onclick={() => startEdit(recap)}
                      class="btn btn-secondary text-xs"
                    >
                      EDIT
                    </button>
                    <button
                      onclick={() => regenerate(recap.id)}
                      disabled={regeneratingId === recap.id}
                      class="btn btn-primary text-xs"
                    >
                      {regeneratingId === recap.id
                        ? "REGENERATING..."
                        : "REGENERATE"}
                    </button>
                  </div>
                </div>

                <div
                  class="p-4"
                >
                  {#if recap.content}
                    <div
                      class="relative"
                    >
                      <div
                        class="hidden"
                      ></div>
                      <p
                        class="font-mono text-xs text-(--color-text-muted) whitespace-pre-wrap line-clamp-6"
                      >
                        {recap.content}
                      </p>
                    </div>
                  {:else}
                    <p
                      class="font-mono text-xs text-(--color-danger)"
                    >
                      [!] NO CONTENT — FATAL GENERATION ERROR
                    </p>
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        </div>
      {/if}

      {#if managerRecaps.length > 0}
        <div>
          <h3
            class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text-muted) mb-3"
          >
            MANAGER SPECIFIC REPORTS
          </h3>
          <div class="space-y-4">
            {#each managerRecaps as recap (recap.id)}
              <div
                class="card overflow-hidden"
              >
                <div
                  class="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 p-4 border-b border-(--color-border-subtle)"
                >
                  <div class="min-w-0">
                    <div class="flex flex-wrap items-center gap-2 mb-1">
                      <span
                        class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text)"
                      >
                        MANAGER: {recap.team_name ?? "UNKNOWN"}
                      </span>
                      <span
                        class="badge {statusBadgeClass(recap.status)}"
                      >
                        {recap.status}
                      </span>
                    </div>
                    <div
                      class="font-mono text-[0.55rem] text-(--color-text-muted) flex flex-wrap gap-2"
                    >
                      <span>[TOKENS: {recap.tokens_used ?? 0}]</span>
                      {#if recap.cost_usd}<span>[COST: ${recap.cost_usd}]</span
                        >{/if}
                      {#if recap.published_at}<span
                          >[TS: {new Date(
                            recap.published_at,
                          ).toISOString()}]</span
                        >{/if}
                    </div>
                  </div>

                  <div class="flex gap-2 shrink-0">
                    <button
                      onclick={() => startEdit(recap)}
                      class="btn btn-secondary text-xs"
                    >
                      EDIT
                    </button>
                    <button
                      onclick={() => regenerate(recap.id)}
                      disabled={regeneratingId === recap.id}
                      class="btn btn-primary text-xs"
                    >
                      {regeneratingId === recap.id
                        ? "REGENERATING..."
                        : "REGENERATE"}
                    </button>
                  </div>
                </div>

                <div
                  class="p-4"
                >
                  {#if recap.content}
                    <div
                      class="relative"
                    >
                      <div
                        class="hidden"
                      ></div>
                      <p
                        class="font-mono text-xs text-(--color-text-muted) whitespace-pre-wrap line-clamp-6"
                      >
                        {recap.content}
                      </p>
                    </div>
                  {:else}
                    <p
                      class="font-mono text-xs text-(--color-danger)"
                    >
                      [!] NO CONTENT — FATAL GENERATION ERROR
                    </p>
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        </div>
      {/if}
    {/if}
  </main>

  <!-- Archive Sidebar - Order 2 on mobile, 1 on desktop -->
  <aside class="order-1 lg:order-2">
    <div class="mb-3">
      <h3
        class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text-muted)"
      >
        REPORT ARCHIVE
      </h3>
    </div>

    <!-- Multi-select / History Grid -->
    <div class="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-3 gap-1.5">
      {#each Array.from({ length: maxWeek }, (_, i) => i + 1) as w}
        {@const hasHistory = history.includes(w)}
        <button
          onclick={() => loadRecaps(w)}
          class="aspect-square flex flex-col items-center justify-center font-mono transition-all duration-200 group border-2 relative cursor-pointer
          {selectedWeek === w
            ? 'bg-(--color-accent-primary) text-(--color-text-inverse) border-(--color-accent-amber) shadow-[2px_2px_0px_rgba(100,255,100,0.2)]'
            : hasHistory
              ? 'bg-(--color-surface) text-(--color-text-commissioner) border-(--color-text) hover:border-(--color-accent-amber) hover:bg-(--color-surface-raised)'
              : 'bg-(--color-surface-base) text-(--color-text-muted) border-(--color-border-subtle) hover:border-(--color-border) hover:text-(--color-text-muted)'}"
          title="Week {w}"
        >
          <span class="text-xs font-bold"
            >#{w.toString().padStart(2, "0")}</span
          >
          {#if hasHistory && selectedWeek !== w}
            <div
              class="absolute bottom-1 left-1/2 -translate-x-1/2 h-1 w-1 rounded-full bg-(--color-success)"
            ></div>
          {/if}
        </button>
      {/each}
    </div>

    <div
      class="mt-3"
    >
      <div class="flex items-center gap-1.5 mb-1">
        <div class="h-1.5 w-1.5 rounded-full bg-(--color-success)"></div>
        <span
          class="font-mono text-[0.55rem] text-(--color-text-muted)"
          >Published Issue</span
        >
      </div>
      <p
        class="font-mono text-[0.55rem] text-(--color-text-muted) leading-relaxed"
      >
        ADMIN OVERRIDE CONSOLE. DIRECT MARKDOWN ACCESS ENABLED FOR ALL GENERATED
        REPORTS.
      </p>
    </div>
  </aside>
</div>

{#if editingRecap}
  <div
    class="overlay"
    role="dialog"
    aria-modal="true"
    aria-labelledby="edit-recap-title"
    tabindex="-1"
    onkeydown={(e) => e.key === "Escape" && cancelEdit()}
  >
    <div
      class="card w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden"
    >
      <div
        class="flex items-center justify-between p-4 border-b border-(--color-border)"
      >
        <h2
          id="edit-recap-title"
          class="font-mono text-sm font-bold tracking-widest uppercase text-(--color-text)"
        >
          // REPORT EDITOR: {editingRecap.type === "league"
            ? "LEAGUE"
            : editingRecap.team_name} //
        </h2>
        <button
          onclick={cancelEdit}
          class="btn btn-secondary text-xs"
        >
          [X] CLOSE
        </button>
      </div>
      <div class="p-4 flex-1 overflow-y-auto space-y-4">
        <div
          class="font-mono text-[0.6rem] text-(--color-text-muted)"
        >
          MD FORMAT REQUIRED. SYSTEM ACCEPTS STANDARD MARKDOWN TOKENS.
        </div>
        <textarea
          bind:value={editContent}
          rows="18"
          class="input w-full font-mono text-xs"
          aria-label="Recap markdown content"
          spellcheck="false"
        ></textarea>
        <div class="flex justify-end gap-2">
          <button
            onclick={cancelEdit}
            class="btn btn-secondary"
          >
            ABORT
          </button>
          <button
            onclick={saveEdit}
            disabled={saving}
            class="btn btn-primary"
          >
            {saving ? "COMMITTING..." : "COMMIT OVERRIDE"}
          </button>
        </div>
      </div>
    </div>
  </div>
{/if}
