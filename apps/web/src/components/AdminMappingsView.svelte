<!--
  Admin Mappings View - Player ID mapping management

  Admin interface for managing player ID mappings between Yahoo,
  MLB, and Lahman datasets with status tracking.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";

  interface Mapping {
    id: number;
    yahoo_player_key: string;
    player_name: string;
    mlb_id: number | null;
    lahman_id: string | null;
    source_confidence: number;
    auto_mapped: boolean;
    status: string;
    notes: string | null;
  }

  let mappings = $state<Mapping[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let successMessage = $state<string | null>(null);
  let search = $state("");
  let filterStatus = $state("all");
  let confirmingAll = $state(false);

  let editingId = $state<number | null>(null);
  let editMlbId = $state<string>("");
  let editLahmanId = $state<string>("");
  let editNotes = $state<string>("");
  let saving = $state(false);

  const allStatuses = ["all", "confirmed", "ambiguous", "manual"];

  /**
   * Fetches the current list of player mappings from the API.
   * Runs automatically on component mount and after batch edits.
   */
  async function loadMappings() {
    loading = true;
    error = null;
    try {
      mappings = await api.get<Mapping[]>("/players/mappings");
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  /**
   * Executes a batch operation to bulk-confirm all auto-mapped ambiguous players
   * across the platform, updating the UI payload afterwards.
   */
  async function confirmAll() {
    confirmingAll = true;
    try {
      const res = await api.post<{ confirmed: number }>(
        "/admin/mappings/confirm-all",
      );
      successMessage = `Confirmed ${res.confirmed} auto-mappings`;
      setTimeout(() => (successMessage = null), 4000);
      await loadMappings();
    } catch (e: any) {
      error = e.message;
    } finally {
      confirmingAll = false;
    }
  }

  /**
   * Initializes the inline-editing UI state for a specific mapping record.
   * @param {Mapping} m - The mapped record to start editing
   */
  function startEdit(m: Mapping) {
    editingId = m.id;
    editMlbId = m.mlb_id ? String(m.mlb_id) : "";
    editLahmanId = m.lahman_id ?? "";
    editNotes = m.notes ?? "";
  }

  /** Resets inline-editing UI state. */
  function cancelEdit() {
    editingId = null;
  }

  /**
   * Validates and submits user-edited ID overrides for a player to the API,
   * transitioning their status to manual.
   * @param {Mapping} m - The targeted player mapping to save
   */
  async function saveMapping(m: Mapping) {
    saving = true;
    error = null;
    try {
      await api.put(`/players/mappings/${m.id}`, {
        mlb_id: editMlbId ? parseInt(editMlbId) : null,
        lahman_id: editLahmanId || null,
        status: "manual",
        notes: editNotes || null,
      });
      const idx = mappings.findIndex((x) => x.id === m.id);
      if (idx !== -1) {
        mappings[idx] = {
          ...mappings[idx],
          mlb_id: editMlbId ? parseInt(editMlbId) : null,
          lahman_id: editLahmanId || null,
          status: "manual",
          notes: editNotes || null,
        };
      }
      editingId = null;
      successMessage = "Mapping updated";
      setTimeout(() => (successMessage = null), 3000);
    } catch (e: any) {
      error = e.message;
    } finally {
      saving = false;
    }
  }

  /**
   * Formally confirms an ambiguous mapping as correct without editing its values.
   * @param {Mapping} m - The mapping record to confirm
   */
  async function confirmMapping(m: Mapping) {
    try {
      await api.put(`/players/mappings/${m.id}`, {
        mlb_id: m.mlb_id,
        lahman_id: m.lahman_id,
        status: "confirmed",
        notes: m.notes,
      });
      const idx = mappings.findIndex((x) => x.id === m.id);
      if (idx !== -1) mappings[idx] = { ...mappings[idx], status: "confirmed" };
      successMessage = `${m.player_name} confirmed`;
      setTimeout(() => (successMessage = null), 2000);
    } catch (e: any) {
      error = e.message;
    }
  }

  /**
   * Derives Tailwind text color classes explicitly based on automated mapping confidence metrics.
   * @param {number} c - The raw confidence ratio (0 to 1) provided by the server
   * @returns {string} Tailwind text classes
   */
  function confidenceColor(c: number) {
    if (c >= 0.9) return "text-success";
    if (c >= 0.7) return "text-warning";
    return "text-danger";
  }

  /**
   * Resolves visual styling configuration for a given mapping's state.
   * @param {string} s - Mapping status enum string
   * @returns {string} Explicit Tailwind visual classes for badges
   */
  function statusBadge(s: string) {
    if (s === "confirmed")
      return "bg-(--color-success-muted) text-(--color-success) border-(--color-success)";
    if (s === "ambiguous")
      return "bg-(--color-warning-muted) text-(--color-warning) border-(--color-warning)";
    if (s === "manual")
      return "bg-(--color-info-muted) text-(--color-info) border-(--color-info)";
    return "bg-(--color-surface-raised) text-(--color-text-muted) border-(--color-border)";
  }

  let filtered = $derived(
    mappings.filter((m) => {
      const matchSearch =
        !search ||
        m.player_name.toLowerCase().includes(search.toLowerCase()) ||
        m.yahoo_player_key.toLowerCase().includes(search.toLowerCase());
      const matchStatus = filterStatus === "all" || m.status === filterStatus;
      return matchSearch && matchStatus;
    }),
  );

  let ambiguousCount = $derived(
    mappings.filter((m) => m.status === "ambiguous").length,
  );

  onMount(loadMappings);
</script>

<div class="space-y-4">
  <div
    class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3"
  >
    <div class="flex flex-col sm:flex-row gap-3">
      <div class="flex items-center gap-2">
        <span
          class="font-mono text-[0.6rem] font-bold tracking-widest text-(--color-text-muted)"
          >[QUERY]</span
        >
        <input
          type="search"
          placeholder="ENTER IDENTIFIER..."
          bind:value={search}
          class="input font-mono text-xs w-48"
          aria-label="Search player mappings"
        />
      </div>
      <div class="flex items-center gap-2 relative">
        <span
          class="font-mono text-[0.6rem] font-bold tracking-widest text-(--color-text-muted)"
          >[STATUS]</span
        >
        <select
          bind:value={filterStatus}
          class="input font-mono text-xs appearance-none pr-8"
          aria-label="Filter by status"
        >
          {#each allStatuses as s}
            <option value={s}>
              {s === "all" ? "ALL STATUSES" : s.toUpperCase()}
            </option>
          {/each}
        </select>
        <div
          class="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-(--color-text-muted) text-xs"
        >
          ▼
        </div>
      </div>
    </div>

    {#if ambiguousCount > 0}
      <button
        onclick={confirmAll}
        disabled={confirmingAll}
        class="btn btn-primary text-xs"
      >
        {confirmingAll
          ? "EXECUTING BATCH CONFIRM..."
          : `[!] BATCH CONFIRM (${ambiguousCount})`}
      </button>
    {/if}
  </div>

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
  {#if loading}
    <div class="space-y-2">
      {#each [1, 2, 3, 4, 5] as _}
        <div class="skeleton h-10 w-full rounded-sm"></div>
      {/each}
    </div>
  {:else if filtered.length === 0}
    <div class="card p-8 text-center">
      <p class="font-mono text-sm text-(--color-text-muted)">
        // NO ENTITIES DETECTED //
      </p>
    </div>
  {:else}
    <div class="card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-(--color-border)">
            <th
              class="px-4 py-3 text-left font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
              >PLAYER IDENTITY</th
            >
            <th
              class="px-3 py-3 text-left font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted) hidden md:table-cell"
              >SYS_KEY (YAHOO)</th
            >
            <th
              class="px-3 py-3 text-center font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
              >SYS_KEY (MLB)</th
            >

            <th
              class="px-3 py-3 text-center font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted) hidden sm:table-cell"
              >CONF_LVL</th
            >
            <th
              class="px-3 py-3 text-center font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
              >STATE</th
            >
            <th
              class="px-3 py-3 text-right font-mono text-[0.65rem] font-bold tracking-widest uppercase text-(--color-text-muted)"
              >OVERRIDE</th
            >
          </tr>
        </thead>
        <tbody class="divide-y divide-(--color-border-subtle)">
          {#each filtered as m (m.id)}
            <tr
              class="hover:bg-(--color-surface-raised) group {m.status ===
              'ambiguous'
                ? 'bg-(--color-surface-raised)'
                : ''}"
            >
              <td class="px-4 py-3">
                <span
                  class="font-display text-sm font-bold text-(--color-text)"
                >
                  {m.player_name}
                </span>
              </td>
              <td
                class="px-3 py-3 font-mono text-xs text-(--color-text-muted) hidden md:table-cell"
              >
                {m.yahoo_player_key}
              </td>

              {#if editingId === m.id}
                <td class="px-3 py-3 text-center">
                  <input
                    type="number"
                    bind:value={editMlbId}
                    placeholder="MLB_ID"
                    class="input font-mono text-xs w-24 text-center"
                    aria-label="Edit MLB ID"
                  />
                </td>
              {:else}
                <td
                  class="px-3 py-3 text-center font-mono text-xs text-(--color-text-muted)"
                >
                  {m.mlb_id ?? "NULL"}
                </td>
              {/if}

              <td class="px-3 py-3 text-center hidden sm:table-cell">
                <span
                  class="font-mono text-xs font-black {confidenceColor(
                    m.source_confidence,
                  )}"
                >
                  {(m.source_confidence * 100).toFixed(0)}%
                </span>
              </td>

              <td class="px-3 py-3 text-center">
                <span class="badge {statusBadge(m.status)}">
                  {m.status}
                </span>
              </td>

              <td class="px-3 py-3 text-right">
                {#if editingId === m.id}
                  <div class="flex gap-1.5 justify-end">
                    <button
                      onclick={cancelEdit}
                      class="btn btn-secondary text-xs px-2 py-1"
                    >
                      ABORT
                    </button>
                    <button
                      onclick={() => saveMapping(m)}
                      disabled={saving}
                      class="btn btn-primary text-xs px-2 py-1"
                    >
                      COMMIT
                    </button>
                  </div>
                {:else}
                  <div class="flex gap-1.5 justify-end">
                    {#if m.status === "ambiguous"}
                      <button
                        onclick={() => confirmMapping(m)}
                        class="btn btn-success text-xs px-2 py-1"
                      >
                        CONFIRM
                      </button>
                    {/if}
                    <button
                      onclick={() => startEdit(m)}
                      class="btn btn-secondary text-xs px-2 py-1"
                    >
                      EDIT
                    </button>
                  </div>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <div
      class="flex items-center justify-between font-mono text-[0.55rem] text-(--color-text-muted) px-4 py-2"
    >
      <span>// EOF //</span>
      <span>RESULTS: {filtered.length} / {mappings.length}</span>
    </div>
  {/if}
</div>
