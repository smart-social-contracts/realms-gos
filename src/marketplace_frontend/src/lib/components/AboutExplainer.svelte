<script lang="ts">
  import { tick } from "svelte";
  import { _ } from "svelte-i18n";
  import { askAshoka, fetchAshokaSuggestions, SignedOutError, type StreamState } from "$lib/ashoka";

  export let demoUrl: string;
  export let docsUrl: string;
  export let contactHref: string;

  type Msg = {
    id: number;
    from: "user" | "assistant";
    text: string;
  };

  let messages: Msg[] = [];
  let nextId = 1;
  let draft = "";
  let threadEl: HTMLDivElement;
  let pending = false;
  let status = "";
  let error = "";
  let followUps: string[] = [];

  const SUGGESTION_KEYS = ["gos", "ssc", "who", "try"] as const;

  $: lastMsg = messages[messages.length - 1];
  $: showPending = pending && !(lastMsg && lastMsg.from === "assistant" && lastMsg.text);
  $: starters = SUGGESTION_KEYS.map((key) => $_("about.explainer.suggestions." + key));
  $: chips = followUps.length ? followUps : starters;

  async function scrollToEnd() {
    await tick();
    if (threadEl) threadEl.scrollTop = threadEl.scrollHeight;
  }

  function upsertAssistant(text: string) {
    const last = messages[messages.length - 1];
    if (!last || last.from === "user") {
      messages = [...messages, { id: nextId++, from: "assistant", text }];
    } else {
      messages = messages.map((m, i) => (i === messages.length - 1 ? { ...m, text } : m));
    }
  }

  function onStream(state: StreamState) {
    status = state.status;
    if (state.text) upsertAssistant(state.text);
    void scrollToEnd();
  }

  async function submit(text: string) {
    const trimmed = text.trim();
    if (!trimmed || pending) return;
    error = "";
    status = "";
    messages = [...messages, { id: nextId++, from: "user", text: trimmed }];
    draft = "";
    pending = true;
    status = $_("about.explainer.waking");
    await scrollToEnd();

    try {
      const reply = await askAshoka({ question: trimmed, onUpdate: onStream });
      if (!reply) {
        upsertAssistant($_("about.explainer.noResponse"));
      } else {
        const next = await fetchAshokaSuggestions({});
        if (next.length) followUps = next;
      }
    } catch (err) {
      error =
        err instanceof SignedOutError
          ? $_("about.explainer.signedOut")
          : err instanceof Error
            ? err.message
            : $_("about.explainer.error");
      const last = messages[messages.length - 1];
      if (last && last.from === "assistant" && !last.text.trim()) {
        messages = messages.slice(0, -1);
      }
    } finally {
      pending = false;
      status = "";
      await scrollToEnd();
    }
  }

  function onSubmit(e: Event) {
    e.preventDefault();
    void submit(draft);
  }

  function onChip(question: string) {
    void submit(question);
  }
</script>

<section class="explainer" aria-label={$_("about.explainer.aria")}>
  <div class="thread" bind:this={threadEl} role="log" aria-live="polite" aria-relevant="additions">
    <div class="turn assistant">
      <p>{$_("about.explainer.greeting")}</p>
    </div>
    {#each messages as m (m.id)}
      <div class="turn {m.from}">
        <p>{m.text}</p>
      </div>
    {/each}
    {#if showPending}
      <p class="pending">{status || "···"}</p>
    {/if}
    {#if error}
      <p class="err" role="alert">{error}</p>
    {/if}
  </div>

  <div class="composer">
    {#if chips.length && !pending}
      <div class="chips" role="group" aria-label={$_("about.explainer.askGoal")}>
        {#each chips as question (question)}
          <button type="button" class="chip" on:click={() => onChip(question)}>
            {question}
          </button>
        {/each}
      </div>
    {/if}
    <form class="input-row" on:submit={onSubmit}>
      <input
        type="text"
        bind:value={draft}
        placeholder={$_("about.explainer.placeholder")}
        aria-label={$_("about.explainer.placeholder")}
        autocomplete="off"
        disabled={pending}
      />
      <button type="submit" class="send" disabled={pending || !draft.trim()}>
        {$_("about.explainer.send")}
      </button>
    </form>
    <p class="links">
      <a href={demoUrl} target="_blank" rel="noreferrer">{$_("about.hero.tryDemo")}</a>
      <a href={docsUrl} target="_blank" rel="noreferrer">{$_("about.getStarted.developers.button")}</a>
      <a href={contactHref}>{$_("about.getStarted.contact.button")}</a>
    </p>
  </div>
</section>

<style>
  .explainer {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
  }
  .thread {
    flex: 1;
    overflow-y: auto;
    padding: 0.25rem 0 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 1.15rem;
  }
  .turn { max-width: 42rem; }
  .turn.user {
    align-self: flex-end;
    max-width: 34rem;
    padding: 0.7rem 0.95rem;
    border-radius: 1rem 1rem 0.25rem 1rem;
    background: var(--surface-2);
  }
  .turn p {
    margin: 0;
    color: var(--text-muted);
    line-height: 1.55;
    white-space: pre-wrap;
  }
  .turn.user p { color: var(--text); }
  .pending {
    margin: 0;
    color: var(--text-faint);
    font-size: 0.9rem;
  }
  .err {
    margin: 0;
    color: var(--danger);
    font-size: 0.9rem;
  }
  .composer {
    position: sticky;
    bottom: 0;
    padding: 0.75rem 0 0;
    background: linear-gradient(#ffffff00, #fff 18%);
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    margin: 0 0 0.75rem;
    max-width: 42rem;
  }
  .chip {
    appearance: none;
    border: 1px solid var(--border);
    background: var(--surface-2);
    color: var(--text);
    border-radius: 999px;
    padding: 0.4rem 0.8rem;
    font-size: 0.85rem;
    line-height: 1.3;
    cursor: pointer;
    text-align: left;
  }
  .chip:hover {
    border-color: var(--border-strong);
    background: #fff;
  }
  .input-row {
    display: flex;
    gap: 0.5rem;
    max-width: 42rem;
  }
  .input-row input {
    flex: 1;
    min-width: 0;
    padding: 0.7rem 0.9rem;
    border: 1px solid var(--border);
    border-radius: 0.7rem;
    background: var(--surface-2);
    font-size: 0.95rem;
    color: var(--text);
  }
  .input-row input:focus {
    outline: none;
    border-color: var(--border-strong);
    background: #fff;
  }
  .send {
    appearance: none;
    border: 1px solid var(--primary);
    background: var(--primary);
    color: #fff;
    border-radius: 0.7rem;
    padding: 0 1rem;
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
  }
  .send:hover { background: var(--primary-hover); }
  .send:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .links {
    display: flex;
    flex-wrap: wrap;
    gap: 0.9rem 1.25rem;
    margin: 0.85rem 0 0;
    font-size: 0.85rem;
  }
  .links a {
    color: var(--text);
    text-underline-offset: 0.18em;
  }
</style>
