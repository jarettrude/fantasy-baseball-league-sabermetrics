<script lang="ts">
  let { data = [], width = 60, height = 24 } = $props<{ data: number[], width?: number, height?: number }>();

  let pathData = $derived.by(() => {
    if (data && data.length > 1) {
      const min = Math.min(...data);
      const max = Math.max(...data);
      const range = max - min || 1;
      
      const pts = data.map((d, i) => {
        const x = (i / (data.length - 1)) * width;
        // Padding top and bottom so stroke isn't cut off
        const padding = 2;
        const drawHeight = height - (padding * 2);
        const y = padding + drawHeight - ((d - min) / range) * drawHeight;
        return `${x},${y}`;
      });
      
      return `M ${pts.join(" L ")}`;
    }
    return "";
  });

  let isPositive = $derived(data && data.length > 0 ? data[data.length - 1] >= data[0] : false);
</script>

{#if pathData}
<svg viewBox="0 0 {width} {height}" {width} {height} class="overflow-visible" preserveAspectRatio="none">
    <path 
        d={pathData} 
        fill="none" 
        stroke={isPositive ? "var(--color-success)" : "var(--color-danger)"} 
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
        class="opacity-80"
        style="filter: drop-shadow(0px 1px 1px rgba(0,0,0,0.1));"
    />
</svg>
{:else}
<div style="width: {width}px; height: {height}px" class="flex items-center justify-center">
    <div class="w-full border-b border-dashed border-(--color-border-subtle)"></div>
</div>
{/if}
