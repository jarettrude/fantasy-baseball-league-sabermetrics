<!--
  Admin Settings View - Configuration management

  Admin interface for managing league configuration, system settings,
  and administrative parameters.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";

  interface AISettings {
    primary_model: string;
    fallback_model: string;
    league_recap_prompt: string;
    manager_recap_prompt: string;
    manager_briefing_prompt: string;
    guardrails: string;
  }

  let aiSettings = $state<AISettings | null>(null);
  let loadingSettings = $state(true);
  let savingSettings = $state(false);
  let saveSuccess = $state(false);
  let saveError = $state<string | null>(null);

  /**
   * Fetches the current AI system configuration and prompt templates from the API.
   */
  async function loadSettings() {
    loadingSettings = true;
    try {
      aiSettings = await api.get<AISettings>("/admin/ai/settings");
    } catch (e) {
      console.error("Failed to load AI settings", e);
    } finally {
      loadingSettings = false;
    }
  }

  /**
   * Submits the commissioner's updated prompt templates and guardrails to the persistence layer.
   */
  async function saveSettings() {
    if (!aiSettings) return;
    savingSettings = true;
    saveSuccess = false;
    saveError = null;
    try {
      await api.put("/admin/ai/settings", {
        league_recap_prompt: aiSettings.league_recap_prompt,
        manager_recap_prompt: aiSettings.manager_recap_prompt,
        manager_briefing_prompt: aiSettings.manager_briefing_prompt,
        guardrails: aiSettings.guardrails,
      });
      saveSuccess = true;
      setTimeout(() => (saveSuccess = false), 3000);
    } catch (e: any) {
      saveError = e.message || "Save failed";
    } finally {
      savingSettings = false;
    }
  }

  onMount(loadSettings);
</script>

<div class="space-y-8">
  {#if aiSettings}
    <section class="space-y-4">
      <div
        class="flex items-center gap-3"
      >
        <span
          class="font-mono text-[0.55rem] font-bold tracking-widest text-(--color-text-muted)"
          >01</span
        >
        <h2 class="font-display text-xl font-extrabold tracking-tight text-(--color-text)">
          Scouting Configuration
        </h2>
      </div>

      <div
        class="card p-5 space-y-4"
      >
        <div
          class="badge badge-success"
        >
          NODE STATUS: ONLINE
        </div>

        <div class="flex flex-col sm:flex-row gap-4">
          <div>
            <span
              class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) block mb-1"
              >PRIMARY NEURAL NET</span
            >
            <span class="font-mono text-sm text-(--color-text)"
              >{aiSettings.primary_model}</span
            >
          </div>
          <div class="hidden sm:block w-px bg-(--color-border)"></div>
          <div>
            <span
              class="font-mono text-[0.6rem] font-bold tracking-widest uppercase text-(--color-text-muted) block mb-1"
              >FALLBACK SYSTEM</span
            >
            <span class="font-mono text-sm text-(--color-text)"
              >{aiSettings.fallback_model}</span
            >
          </div>
        </div>

        <div
          class="font-mono text-[0.55rem] text-(--color-text-muted) flex items-center gap-1"
        >
          <span class="hidden"></span>
          <span
            >SYSTEM BEHAVIOR: 3 RETRIES (EXPONENTIAL BACKOFF) PRIOR TO FALLBACK.</span
          >
        </div>
      </div>
    </section>

    <section class="space-y-4">
      <div
        class="flex items-center justify-between"
      >
        <div class="flex items-center gap-3">
          <span
            class="font-mono text-[0.55rem] font-bold tracking-widest text-(--color-text-muted)"
            >02</span
          >
          <h2 class="font-display text-xl font-extrabold tracking-tight text-(--color-text)">
            Report Templates
          </h2>
        </div>
        {#if saveSuccess}
          <span
            class="badge badge-success"
          >
            SAVED SUCCESSFULLY
          </span>
        {/if}
        {#if saveError}
          <span
            class="badge badge-danger"
          >
            {saveError}
          </span>
        {/if}
      </div>

      <div class="space-y-6">
        <div
          class="space-y-2"
        >
          <label
            for="guardrails"
            class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text-muted) flex items-center gap-1.5"
          >
            <span class="hidden"></span>
            Global Guardrails
          </label>
          <textarea
            id="guardrails"
            bind:value={aiSettings.guardrails}
            rows="6"
            placeholder="SFW, light-hearted, no personal degradation..."
            class="input w-full font-mono text-xs"
          ></textarea>
        </div>

        <div
          class="space-y-2"
        >
          <label
            for="league-prompt"
            class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text-muted) flex items-center gap-1.5"
          >
            <span class="hidden"></span>
            League Recap Report
          </label>
          <textarea
            id="league-prompt"
            bind:value={aiSettings.league_recap_prompt}
            rows="12"
            class="input w-full font-mono text-xs"
          ></textarea>
        </div>

        <div
          class="space-y-2"
        >
          <label
            for="manager-prompt"
            class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text-muted) flex items-center gap-1.5"
          >
            <span class="hidden"></span>
            Manager Recap Report
          </label>
          <textarea
            id="manager-prompt"
            bind:value={aiSettings.manager_recap_prompt}
            rows="12"
            class="input w-full font-mono text-xs"
          ></textarea>
        </div>

        <div
          class="space-y-2"
        >
          <label
            for="briefing-prompt"
            class="font-mono text-xs font-bold tracking-widest uppercase text-(--color-text-muted) flex items-center gap-1.5"
          >
            <span class="hidden"></span>
            Daily Briefing Report
          </label>
          <textarea
            id="briefing-prompt"
            bind:value={aiSettings.manager_briefing_prompt}
            rows="12"
            class="input w-full font-mono text-xs"
          ></textarea>
        </div>

        <div class="flex justify-end">
          <button
            onclick={saveSettings}
            disabled={savingSettings}
            class="btn btn-primary"
          >
            {savingSettings ? "COMMITTING LOGIC..." : "COMMIT REPORTS"}
          </button>
        </div>
      </div>
    </section>
  {:else if loadingSettings}
    <div class="space-y-4">
      {#each [1, 2, 3] as _}
        <div
          class="skeleton h-32 w-full rounded-sm"
        ></div>
      {/each}
    </div>
  {/if}
</div>
