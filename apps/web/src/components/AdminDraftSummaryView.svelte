<!--
  Admin Draft Summary View - Draft analysis management

  Admin interface for viewing AI-generated draft summaries, editing the prompt,
  importing draft picks, and regenerating the analysis.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";

  interface DraftPick {
    pick_number: number;
    round_number: number;
    round_pick: number;
    player_name: string;
    player_position: string | null;
  }

  interface TeamDraft {
    team_name: string;
    picks: DraftPick[];
  }

  interface AvailablePlayer {
    name: string;
    position: string;
    yahoo_rank: number | null;
    team: string | null;
    composite_value: number;
    our_rank: number | null;
  }

  interface DraftSummary {
    id: number | null;
    season: number;
    status: string;
    content: string | null;
    model_used: string | null;
    provider_used: string | null;
    tokens_used: number | null;
    cost_usd: string | null;
    created_at: string | null;
    updated_at: string | null;
    teams_draft: TeamDraft[];
    available_players: AvailablePlayer[];
  }

  interface AISettings {
    draft_summary_prompt: string;
  }

  type ActiveTab = "summary" | "picks" | "available" | "prompt";

  const tabs: { id: ActiveTab; label: string }[] = [
    { id: "summary", label: "AI SUMMARY" },
    { id: "picks", label: "DRAFT BOARD" },
    { id: "available", label: "LEFT ON BOARD" },
    { id: "prompt", label: "PROMPT EDITOR" },
  ];

  let summary = $state<DraftSummary | null>(null);
  let loading = $state(false);
  let generating = $state(false);
  let error = $state<string | null>(null);
  let successMessage = $state<string | null>(null);

  let activeTab = $state<ActiveTab>("summary");

  let editingContent = $state(false);
  let editContent = $state("");
  let saving = $state(false);

  let promptText = $state("");
  let loadingPrompt = $state(false);
  let savingPrompt = $state(false);
  let promptSaved = $state(false);

  let expandedTeam = $state<string | null>(null);
  let syncingDraft = $state(false);
  let syncResult = $state<{ imported: number; skipped: number; errors: string[] } | null>(null);

  /**
   * Pull draft picks and player data directly from Yahoo Fantasy Sports API.
   */
  async function syncFromYahoo() {
    syncingDraft = true;
    error = null;
    syncResult = null;
    try {
      const result = await api.post<{
        imported: number;
        skipped: number;
        errors: string[];
      }>("/admin/draft-picks/sync");
      syncResult = result;
      successMessage = `Synced ${result.imported} picks from Yahoo${result.skipped ? ` (${result.skipped} skipped)` : ""}`;
      setTimeout(() => (successMessage = null), 5000);
      await loadSummary();
    } catch (e: any) {
      error = e.message;
    } finally {
      syncingDraft = false;
    }
  }

  /**
   * Load the draft summary and pick data from the API.
   */
  async function loadSummary() {
    loading = true;
    error = null;
    try {
      summary = await api.get<DraftSummary>("/admin/draft-summary");
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  /**
   * Load the current draft summary prompt template.
   */
  async function loadPrompt() {
    loadingPrompt = true;
    try {
      const settings = await api.get<AISettings>("/admin/ai/settings");
      promptText = settings.draft_summary_prompt;
    } catch (e: any) {
      error = e.message;
    } finally {
      loadingPrompt = false;
    }
  }

  /**
   * Save the edited draft summary prompt template.
   */
  async function savePrompt() {
    savingPrompt = true;
    promptSaved = false;
    try {
      await api.put("/admin/ai/settings", { draft_summary_prompt: promptText });
      promptSaved = true;
      setTimeout(() => (promptSaved = false), 3000);
    } catch (e: any) {
      error = e.message;
    } finally {
      savingPrompt = false;
    }
  }

  /**
   * Trigger AI generation of the draft summary.
   * @param force - If true, regenerate even if one already exists
   */
  async function generateSummary(force: boolean = false) {
    generating = true;
    error = null;
    try {
      await api.post(`/admin/draft-summary/generate?force=${force}`);
      successMessage = "Draft summary generated successfully";
      setTimeout(() => (successMessage = null), 4000);
      await loadSummary();
    } catch (e: any) {
      error = e.message;
    } finally {
      generating = false;
    }
  }

  /**
   * Open the inline content editor for the current summary.
   */
  function startEdit() {
    if (!summary?.content) return;
    editContent = summary.content;
    editingContent = true;
  }

  /** Close the content editor without saving. */
  function cancelEdit() {
    editingContent = false;
    editContent = "";
  }

  /**
   * Save manual edits to the draft summary content.
   */
  async function saveEdit() {
    if (!summary?.id) return;
    saving = true;
    try {
      const result = await api.put(`/admin/draft-summary/${summary.id}`, {
        content: editContent,
      });
      summary = { ...summary, content: (result as any).content };
      editingContent = false;
      successMessage = "Summary saved";
      setTimeout(() => (successMessage = null), 3000);
    } catch (e: any) {
      error = e.message;
    } finally {
      saving = false;
    }
  }

  /**
   * Toggle expanded view for a team's draft picks.
   * @param teamName - Name of the team to toggle
   */
  function toggleTeam(teamName: string) {
    expandedTeam = expandedTeam === teamName ? null : teamName;
  }

  /**
   * Returns badge class based on draft summary generation status.
   * @param s - Status string
   */
  function statusBadgeClass(s: string) {
    if (s === "published") return "badge-success";
    if (s === "failed") return "badge-danger";
    if (s === "none") return "badge-warning";
    return "badge-warning";
  }

  onMount(async () => {
    await Promise.all([loadSummary(), loadPrompt()]);
  });
</script>

<div class="space-y-6">
  <!-- Header bar -->
  <div
    class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3"
  >
    <div>
      <h2
        class="font-display text-xl font-extrabold tracking-tight text-(--color-text)"
      >
        Draft Analysis
      </h2>
      {#if summary}
        <p class="font-mono text-[0.55rem] text-(--color-text-muted) mt-0.5">
          {summary.teams_draft.length} TEAMS · {summary.teams_draft.reduce(
            (acc, t) => acc + t.picks.length,
            0,
          )} TOTAL PICKS · SEASON {summary.season}
        </p>
      {/if}
    </div>

    <div class="flex gap-2 flex-wrap items-center">
      <!-- Always-visible Yahoo sync button -->
      <button
        onclick={syncFromYahoo}
        disabled={syncingDraft}
        class="btn btn-secondary text-xs flex items-center gap-1.5"
      >
        {#if syncingDraft}
          <span class="font-mono text-[0.6rem] animate-pulse">●</span>
          SYNCING FROM YAHOO...
        {:else}
          <span class="font-mono text-[0.6rem]">↓</span>
          SYNC DRAFT FROM YAHOO
        {/if}
      </button>

      {#if summary?.status === "published"}
        <button
          onclick={() => generateSummary(true)}
          disabled={generating || syncingDraft}
          class="btn btn-secondary text-xs"
        >
          {generating ? "REGENERATING..." : "REGENERATE"}
        </button>
        {#if summary.content}
          <button
            onclick={startEdit}
            disabled={syncingDraft}
            class="btn btn-secondary text-xs"
          >
            EDIT CONTENT
          </button>
        {/if}
      {:else}
        <button
          onclick={() => generateSummary(false)}
          disabled={generating || syncingDraft}
          class="btn btn-primary text-xs"
        >
          {generating ? "GENERATING..." : "GENERATE SUMMARY"}
        </button>
      {/if}
    </div>
  </div>

  <!-- Status / feedback banners -->
  {#if error}
    <div
      class="card p-4 border-l-4 border-l-(--color-danger) font-mono text-xs text-(--color-danger)"
    >
      {error}
    </div>
  {/if}

  {#if successMessage}
    <div
      class="card p-4 border-l-4 border-l-(--color-success) font-mono text-xs text-(--color-success)"
    >
      {successMessage}
    </div>
  {/if}

  <!-- Tab nav -->
  <div class="flex overflow-x-auto border-b border-(--color-border)">
    {#each tabs as tab}
      <button
        onclick={() => (activeTab = tab.id)}
        class="whitespace-nowrap px-4 py-2.5 font-mono text-[0.6rem] font-bold uppercase tracking-widest transition-colors duration-200 border-b-2
        {activeTab === tab.id
          ? 'border-(--color-commissioner) text-(--color-commissioner)'
          : 'border-transparent text-(--color-text-muted) hover:text-(--color-text) hover:bg-(--color-surface-raised)'}"
      >
        {tab.label}
      </button>
    {/each}
  </div>

  <!-- Tab content -->
  {#if loading}
    <div class="space-y-4">
      {#each [1, 2, 3] as _}
        <div class="skeleton h-24 w-full"></div>
      {/each}
    </div>

    <!-- AI SUMMARY TAB -->
  {:else if activeTab === "summary"}
    {#if !summary || summary.status === "none"}
      <div class="card p-8 text-center space-y-4">
        <p
          class="font-mono text-sm font-bold tracking-widest text-(--color-text-muted)"
        >
          // NO DRAFT SUMMARY GENERATED //
        </p>
        <p class="font-mono text-[0.6rem] text-(--color-text-muted)">
          Import draft picks first, then click GENERATE SUMMARY to produce the
          AI analysis.
        </p>
        <button
          onclick={() => generateSummary(false)}
          disabled={generating || !summary || summary.teams_draft.length === 0}
          class="btn btn-primary text-xs"
        >
          {generating ? "GENERATING..." : "GENERATE SUMMARY"}
        </button>
      </div>
    {:else if summary.status === "failed"}
      <div
        class="card p-6 border-l-4 border-l-(--color-danger) space-y-3"
      >
        <p class="font-mono text-xs font-bold text-(--color-danger)">
          [!] GENERATION FAILED
        </p>
        <p class="font-mono text-[0.6rem] text-(--color-text-muted)">
          Check AI provider connectivity and try regenerating.
        </p>
        <button
          onclick={() => generateSummary(true)}
          disabled={generating}
          class="btn btn-primary text-xs"
        >
          {generating ? "RETRYING..." : "RETRY GENERATION"}
        </button>
      </div>
    {:else}
      <!-- Meta strip -->
      <div
        class="card p-4 flex flex-wrap items-center gap-4 border-(--color-border)"
      >
        <span class="badge {statusBadgeClass(summary.status)}">
          {summary.status.toUpperCase()}
        </span>
        {#if summary.model_used}
          <span class="font-mono text-[0.55rem] text-(--color-text-muted)"
            >[MODEL: {summary.model_used}]</span
          >
        {/if}
        {#if summary.tokens_used}
          <span class="font-mono text-[0.55rem] text-(--color-text-muted)"
            >[TOKENS: {summary.tokens_used.toLocaleString()}]</span
          >
        {/if}
        {#if summary.cost_usd}
          <span class="font-mono text-[0.55rem] text-(--color-text-muted)"
            >[COST: ${summary.cost_usd}]</span
          >
        {/if}
        {#if summary.updated_at}
          <span class="font-mono text-[0.55rem] text-(--color-text-muted)"
            >[GENERATED: {new Date(summary.updated_at).toLocaleString()}]</span
          >
        {/if}
      </div>

      <!-- The rendered summary -->
      {#if summary.content}
        <div class="card p-6">
          <div
            class="prose prose-sm prose-invert max-w-none
                   [&_h3]:font-mono [&_h3]:text-xs [&_h3]:font-bold [&_h3]:tracking-widest [&_h3]:uppercase [&_h3]:text-(--color-commissioner) [&_h3]:mb-3 [&_h3]:mt-5
                   [&_p]:font-sans [&_p]:text-sm [&_p]:text-(--color-text) [&_p]:leading-relaxed
                   [&_strong]:text-(--color-text) [&_strong]:font-bold
                   [&_ul]:space-y-1 [&_li]:font-sans [&_li]:text-sm [&_li]:text-(--color-text)
                   [&_blockquote]:border-l-2 [&_blockquote]:border-(--color-commissioner) [&_blockquote]:pl-4 [&_blockquote]:text-(--color-text-muted)
                   [&_table]:w-full [&_table]:font-mono [&_table]:text-xs
                   [&_th]:text-(--color-text-muted) [&_th]:text-left [&_th]:pb-2 [&_th]:border-b [&_th]:border-(--color-border)
                   [&_td]:py-1.5 [&_td]:border-b [&_td]:border-(--color-border-subtle) [&_td]:text-(--color-text)"
          >
            {@html summary.content}
          </div>
        </div>
      {/if}
    {/if}

    <!-- DRAFT BOARD TAB -->
  {:else if activeTab === "picks"}
    {#if !summary || summary.teams_draft.length === 0}
      <div class="card p-8 text-center space-y-4">
        <p class="font-mono text-sm font-bold tracking-widest text-(--color-text-muted)">
          // NO DRAFT PICKS LOADED //
        </p>
        <p class="font-mono text-[0.6rem] text-(--color-text-muted)">
          Pull the draft board directly from Yahoo to get started.
        </p>
        <button
          onclick={syncFromYahoo}
          disabled={syncingDraft}
          class="btn btn-primary text-xs mx-auto flex items-center gap-1.5"
        >
          {#if syncingDraft}
            <span class="font-mono text-[0.6rem] animate-pulse">●</span>
            SYNCING FROM YAHOO...
          {:else}
            <span class="font-mono text-[0.6rem]">↓</span>
            SYNC DRAFT FROM YAHOO
          {/if}
        </button>
      </div>
    {:else}
      <div class="space-y-2">
        {#each summary.teams_draft as team (team.team_name)}
          <div class="card overflow-hidden">
            <button
              onclick={() => toggleTeam(team.team_name)}
              class="w-full flex items-center justify-between p-4 text-left hover:bg-(--color-surface-raised) transition-colors"
            >
              <div class="flex items-center gap-3">
                <span
                  class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text)"
                >
                  {team.team_name}
                </span>
                <span
                  class="font-mono text-[0.55rem] text-(--color-text-muted)"
                >
                  {team.picks.length} PICKS
                </span>
              </div>
              <span
                class="font-mono text-[0.6rem] text-(--color-text-muted) transition-transform duration-200"
                class:rotate-180={expandedTeam === team.team_name}
              >
                ▼
              </span>
            </button>

            {#if expandedTeam === team.team_name}
              <div class="border-t border-(--color-border)">
                <table class="w-full">
                  <thead>
                    <tr
                      class="border-b border-(--color-border-subtle) bg-(--color-surface-base)"
                    >
                      <th
                        class="text-left p-3 font-mono text-[0.55rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
                        >#</th
                      >
                      <th
                        class="text-left p-3 font-mono text-[0.55rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
                        >RD</th
                      >
                      <th
                        class="text-left p-3 font-mono text-[0.55rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
                        >PLAYER</th
                      >
                      <th
                        class="text-left p-3 font-mono text-[0.55rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
                        >POS</th
                      >
                    </tr>
                  </thead>
                  <tbody>
                    {#each team.picks as pick (pick.pick_number)}
                      <tr
                        class="border-b border-(--color-border-subtle) hover:bg-(--color-surface-raised) transition-colors"
                      >
                        <td
                          class="p-3 font-mono text-xs text-(--color-text-muted)"
                          >{pick.pick_number}</td
                        >
                        <td
                          class="p-3 font-mono text-[0.6rem] text-(--color-text-muted)"
                          >{pick.round_number}.{String(pick.round_pick).padStart(2, "0")}</td
                        >
                        <td
                          class="p-3 font-mono text-xs font-bold text-(--color-text)"
                          >{pick.player_name}</td
                        >
                        <td class="p-3">
                          {#if pick.player_position}
                            <span
                              class="font-mono text-[0.55rem] font-bold px-1.5 py-0.5 rounded-sm bg-(--color-surface-raised) text-(--color-text-muted)"
                            >
                              {pick.player_position}
                            </span>
                          {/if}
                        </td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}

    <!-- LEFT ON BOARD TAB -->
  {:else if activeTab === "available"}
    {#if !summary || summary.available_players.length === 0}
      <div class="card p-8 text-center">
        <p class="font-mono text-sm text-(--color-text-muted)">
          // NO AVAILABLE PLAYER DATA //
        </p>
        <p class="font-mono text-[0.6rem] text-(--color-text-muted) mt-2">
          Generate a summary to populate this data from the free agent pool.
        </p>
      </div>
    {:else}
      <div class="card overflow-hidden">
        <div
          class="p-4 border-b border-(--color-border) flex items-center gap-2"
        >
          <span
            class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text-muted)"
          >
            TOP {summary.available_players.length} PLAYERS LEFT ON BOARD
          </span>
        </div>
        <table class="w-full">
          <thead>
            <tr
              class="border-b border-(--color-border-subtle) bg-(--color-surface-base)"
            >
              <th
                class="text-left p-3 font-mono text-[0.55rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
                >RANK</th
              >
              <th
                class="text-left p-3 font-mono text-[0.55rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
                >PLAYER</th
              >
              <th
                class="text-left p-3 font-mono text-[0.55rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
                >POS</th
              >
              <th
                class="text-left p-3 font-mono text-[0.55rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
                >TEAM</th
              >
              <th
                class="text-right p-3 font-mono text-[0.55rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
                >VALUE</th
              >
              <th
                class="text-right p-3 font-mono text-[0.55rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
                >YAH. RANK</th
              >
            </tr>
          </thead>
          <tbody>
            {#each summary.available_players as player, i (i)}
              <tr
                class="border-b border-(--color-border-subtle) hover:bg-(--color-surface-raised) transition-colors"
              >
                <td class="p-3 font-mono text-xs text-(--color-text-muted)"
                  >{i + 1}</td
                >
                <td class="p-3 font-mono text-xs font-bold text-(--color-text)"
                  >{player.name}</td
                >
                <td class="p-3">
                  <span
                    class="font-mono text-[0.55rem] font-bold px-1.5 py-0.5 rounded-sm bg-(--color-surface-raised) text-(--color-text-muted)"
                  >
                    {player.position}
                  </span>
                </td>
                <td
                  class="p-3 font-mono text-[0.6rem] text-(--color-text-muted)"
                  >{player.team ?? "—"}</td
                >
                <td
                  class="p-3 font-mono text-xs text-right text-(--color-commissioner) font-bold"
                >
                  {player.composite_value.toFixed(2)}
                </td>
                <td
                  class="p-3 font-mono text-[0.6rem] text-right text-(--color-text-muted)"
                >
                  {player.yahoo_rank ?? "—"}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

    <!-- PROMPT EDITOR TAB -->
  {:else if activeTab === "prompt"}
    <div class="space-y-4">
      <div
        class="font-mono text-[0.55rem] text-(--color-text-muted) leading-relaxed"
      >
        EDIT THE SYSTEM PROMPT USED FOR DRAFT SUMMARY GENERATION. CHANGES TAKE
        EFFECT ON NEXT GENERATION RUN. GUARDRAILS ARE AUTOMATICALLY PREPENDED.
      </div>

      {#if loadingPrompt}
        <div class="skeleton h-64 w-full"></div>
      {:else}
        <div class="space-y-3">
          <textarea
            bind:value={promptText}
            rows="22"
            class="input w-full font-mono text-xs"
            spellcheck="false"
            aria-label="Draft summary prompt template"
          ></textarea>

          <div class="flex items-center justify-between">
            {#if promptSaved}
              <span class="badge badge-success">PROMPT SAVED</span>
            {:else}
              <span></span>
            {/if}
            <button
              onclick={savePrompt}
              disabled={savingPrompt}
              class="btn btn-primary text-xs"
            >
              {savingPrompt ? "COMMITTING..." : "COMMIT PROMPT"}
            </button>
          </div>
        </div>
      {/if}
    </div>
  {/if}
</div>

<!-- Edit modal -->
{#if editingContent}
  <div
    class="overlay"
    role="dialog"
    aria-modal="true"
    aria-labelledby="edit-summary-title"
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
          id="edit-summary-title"
          class="font-mono text-sm font-bold tracking-widest uppercase text-(--color-text)"
        >
          // DRAFT SUMMARY EDITOR //
        </h2>
        <button onclick={cancelEdit} class="btn btn-secondary text-xs">
          [X] CLOSE
        </button>
      </div>
      <div class="p-4 flex-1 overflow-y-auto space-y-4">
        <div class="font-mono text-[0.6rem] text-(--color-text-muted)">
          MD FORMAT REQUIRED. EDITING RAW RENDERED HTML — PASTE MARKDOWN TO
          REPLACE.
        </div>
        <textarea
          bind:value={editContent}
          rows="20"
          class="input w-full font-mono text-xs"
          aria-label="Draft summary content"
          spellcheck="false"
        ></textarea>
        <div class="flex justify-end gap-2">
          <button onclick={cancelEdit} class="btn btn-secondary">ABORT</button>
          <button onclick={saveEdit} disabled={saving} class="btn btn-primary">
            {saving ? "COMMITTING..." : "COMMIT OVERRIDE"}
          </button>
        </div>
      </div>
    </div>
  </div>
{/if}
