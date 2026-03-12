<!--
  Admin Navigation - Admin dashboard sidebar

  Navigation sidebar for admin dashboard sections including overview,
  settings, recaps, briefings, and mappings.
-->
<script lang="ts">
  export let currentPath: string;

  const links = [
    { href: "/admin", label: "Overview", match: "/admin" },
    {
      href: "/admin/mappings",
      label: "Player Mappings",
      match: "/admin/mappings",
    },
    { href: "/admin/settings", label: "AI Settings", match: "/admin/settings" },
    { href: "/admin/recaps", label: "AI Recaps", match: "/admin/recaps" },
    {
      href: "/admin/briefings",
      label: "AI Briefings",
      match: "/admin/briefings",
    },
  ];
</script>

<div class="mb-6">
  <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
    <h1 class="font-display text-2xl font-extrabold tracking-tight text-(--color-text)">
      SYSTEM ADMIN //
    </h1>
    <div>
      <p
        class="font-mono text-[0.6rem] font-bold tracking-widest text-(--color-commissioner)"
      >
        [ COMMISSIONER PRIVILEGES ACTIVE ]
      </p>
    </div>
  </div>

  <div
    class="flex overflow-x-auto border-b border-(--color-border)"
  >
    {#each links as link}
      <a
        href={link.href}
        class="whitespace-nowrap px-4 py-2.5 font-mono text-[0.6rem] font-bold uppercase tracking-widest transition-colors duration-200 border-b-2 {currentPath ===
          link.match ||
        (link.match === '/admin' && currentPath === '/admin/')
          ? 'border-(--color-commissioner) text-(--color-commissioner)'
          : 'border-transparent text-(--color-text-muted) hover:text-(--color-text) hover:bg-(--color-surface-raised)'}"
      >
        {link.label}
      </a>
    {/each}
  </div>
</div>
