<!--
  Hero Actions - Dashboard quick actions

  Hero section with quick action buttons for navigating to key
  dashboard features like standings, matchups, and roster.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { fetchUser, getUser } from "../lib/stores.svelte";

  let user = $derived(getUser());
  let loading = $state(true);

  onMount(async () => {
    await fetchUser();
    loading = false;
  });
</script>

<div class="mt-8 flex flex-wrap gap-3">
  {#if loading}
    <div class="skeleton h-10 w-40 rounded-sm"></div>
  {:else if user}
    <a href="/bench" class="btn btn-primary"> TEAM BENCH // </a>
  {:else}
    <a href="/login" class="btn btn-primary"> CONNECT TO YAHOO // </a>
  {/if}
  <a href="/dashboard#standings" class="btn btn-secondary">
    VIEW STANDINGS //
  </a>
</div>
