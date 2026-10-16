<script lang="ts">import { _ } from "svelte-i18n";
export let status = "unverified";
export let size = "sm";
$: meta = (() => {
  switch (status) {
    case "verified":
      return { label: $_("verified.verified"), cls: "verified" };
    case "pending_audit":
      return { label: $_("verified.pending"), cls: "pending" };
    case "pending_review":
      return { label: $_("verified.in_review"), cls: "pending" };
    case "rejected":
      return { label: $_("verified.rejected"), cls: "rejected" };
    default:
      return null;
  }
})();
</script>

{#if meta}
  <span class="badge {meta.cls} {size}">{meta.label}</span>
{/if}

<style>
  .badge {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 999px;
    font-weight: 500;
    font-size: 0.7rem;
    line-height: 1;
    height: 22px;
    border: 1px solid transparent;
    white-space: nowrap;
  }
  .md { font-size: 0.8rem; height: 26px; padding: 4px 10px; }
  .verified { background: var(--primary); color: #fff; border-color: var(--primary); }
  .pending { background: var(--surface); color: var(--text-muted); border-color: var(--border-strong); }
  .rejected { background: var(--surface); color: var(--text-faint); border-color: var(--border); text-decoration: line-through; }
</style>
