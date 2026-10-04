<script lang="ts">
import { _, json } from "svelte-i18n";
import CapitolBackdrop from "$lib/components/CapitolBackdrop.svelte";
import { CONTACT_MAILTO } from "$lib/contact";

const DEMO_URL = "https://demo.gos.earth";
const INVITE_URL = "https://tally.so/r/GxQ8QL";
const DOCS_URL = "https://github.com/smart-social-contracts/realms-gos/tree/main/docs";

const FEATURES = [
  { title: "about.features.internetComputer.title", desc: "about.features.internetComputer.description" },
  { title: "about.features.secure.title", desc: "about.features.secure.description" },
  { title: "about.features.developerFriendly.title", desc: "about.features.developerFriendly.description" },
  { title: "about.features.multiRealm.title", desc: "about.features.multiRealm.description" },
  { title: "about.features.extensionSystem.title", desc: "about.features.extensionSystem.description" },
  { title: "about.features.instantSetup.title", desc: "about.features.instantSetup.description" },
  { title: "about.features.aiGovernors.title", desc: "about.features.aiGovernors.description" },
];

const PRINCIPLES = [
  { n: "01", title: "about.principles.transparency.title", desc: "about.principles.transparency.description" },
  { n: "02", title: "about.principles.efficiency.title", desc: "about.principles.efficiency.description" },
  { n: "03", title: "about.principles.diversity.title", desc: "about.principles.diversity.description" },
  { n: "04", title: "about.principles.resilience.title", desc: "about.principles.resilience.description" },
];

$: identityFeatures = (Array.isArray($json("about.forPeople.identity.features"))
  ? $json("about.forPeople.identity.features")
  : []) as string[];
$: govBenefits = (Array.isArray($json("about.forInstitutions.governments.benefits"))
  ? $json("about.forInstitutions.governments.benefits")
  : []) as string[];
</script>

<svelte:head>
  <title>About · Realms GOS</title>
</svelte:head>

<article class="about">
  <header class="about-hero">
    <div class="about-hero-bg" aria-hidden="true">
      <CapitolBackdrop />
      <div class="about-hero-wash"></div>
    </div>
    <div class="about-hero-inner">
      <h1>{$_("about.hero.title")}</h1>
      <p class="alpha">{$_("about.hero.alphaBadge")}</p>
    </div>
  </header>

  <div class="about-body">
    <div class="overview">
      <section class="block" aria-labelledby="mission-title">
        <h2 id="mission-title">{$_("about.mission.title")}</h2>
        <p class="prose">{@html $_("about.mission.description")}</p>
        <p class="more">
          <a href={$_("about.mission.learnMoreUrl")} target="_blank" rel="noreferrer">{$_("about.mission.learnMore")}</a>
        </p>
      </section>

      <section class="block" aria-labelledby="principles-title">
        <h2 id="principles-title">{$_("about.principles.title")}</h2>
        <p class="lede">{$_("about.principles.intro")}</p>
        <div class="principles">
          {#each PRINCIPLES as p}
            <div class="principle">
              <div class="num">{p.n}</div>
              <h3>{$_(p.title)}</h3>
              <p>{$_(p.desc)}</p>
            </div>
          {/each}
        </div>
      </section>

      <section class="block" id="features" aria-labelledby="features-title">
        <h2 id="features-title">{$_("about.features.title")}</h2>
        <p class="lede">{$_("about.features.subtitle")}</p>
        <dl class="defs">
          {#each FEATURES as f}
            <div>
              <dt>{$_(f.title)}</dt>
              <dd>{$_(f.desc)}</dd>
            </div>
          {/each}
        </dl>
      </section>

      <section class="audience" aria-label="Who Realms is for">
        <div class="audience-col" id="forpeople" role="region" aria-labelledby="people-title">
          <h2 id="people-title">{$_("about.forPeople.title")}</h2>
          <p class="lede">{$_("about.forPeople.subtitle")}</p>
          <h3>{$_("about.forPeople.identity.title")}</h3>
          <p>{$_("about.forPeople.identity.description")}</p>
          <ul class="plain">
            {#each identityFeatures as item}
              <li>{item}</li>
            {/each}
          </ul>
          <h3>{$_("about.forPeople.participate.title")}</h3>
          <p>{$_("about.forPeople.participate.description")}</p>
          <h3>{$_("about.forPeople.create.title")}</h3>
          <p>{$_("about.forPeople.create.description")}</p>
        </div>
        <div class="audience-col" id="forinstitutions" role="region" aria-labelledby="institutions-title">
          <h2 id="institutions-title">{$_("about.forInstitutions.title")}</h2>
          <p class="lede">{$_("about.forInstitutions.subtitle")}</p>
          <h3>{$_("about.forInstitutions.governments.title")}</h3>
          <p>{$_("about.forInstitutions.governments.description")}</p>
          <p class="kicker">{$_("about.forInstitutions.benefits_heading")}</p>
          <ul class="plain">
            {#each govBenefits as item}
              <li>{item}</li>
            {/each}
          </ul>
          <h3>{$_("about.forInstitutions.organizations.title")}</h3>
          <p>{$_("about.forInstitutions.organizations.description")}</p>
          <h3>{$_("about.forInstitutions.migration.title")}</h3>
          <p>{$_("about.forInstitutions.migration.description")}</p>
        </div>
      </section>

      <section class="block start" id="getstarted" aria-labelledby="started-title">
        <h2 id="started-title">{$_("about.getStarted.title")}</h2>
        <p class="lede">{$_("about.getStarted.subtitle")}</p>
        <div class="start-row">
          <a href={DEMO_URL} target="_blank" rel="noreferrer">
            <strong>{$_("about.getStarted.demo.title")}</strong>
            <span>{$_("about.getStarted.demo.description")}</span>
          </a>
          <a href={CONTACT_MAILTO}>
            <strong>{$_("about.getStarted.contact.title")}</strong>
            <span>{$_("about.getStarted.contact.description")}</span>
          </a>
          <a href={DOCS_URL} target="_blank" rel="noreferrer">
            <strong>{$_("about.getStarted.developers.title")}</strong>
            <span>{$_("about.getStarted.developers.description")}</span>
          </a>
        </div>
        <p class="invite">
          <a href={INVITE_URL} target="_blank" rel="noreferrer">{$_("about.hero.requestInvite")}</a>
        </p>
      </section>
    </div>
  </div>
</article>

<style>
  .about { background: #ffffff; }
  .about-hero {
    position: relative;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    padding: 4.5rem 1.5rem 2.5rem;
  }
  .about-hero-bg {
    position: absolute;
    inset: 0;
    background: #ffffff;
  }
  .about-hero-wash {
    position: absolute;
    inset: 0;
    pointer-events: none;
    background: linear-gradient(#ffffffee, #fffffff5 52%, #fff);
  }
  .about-hero-inner {
    position: relative;
    z-index: 1;
    max-width: 42rem;
    width: 100%;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
  }
  .about-hero h1 {
    margin: 0 0 0.55rem;
    font-size: clamp(1.85rem, 4.2vw, 2.65rem);
    font-weight: 700;
    line-height: 1.15;
    letter-spacing: -0.02em;
    text-wrap: balance;
  }
  .alpha {
    margin: 0;
    font-size: 0.8rem;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-faint);
  }

  .about-body {
    max-width: 52rem;
    margin: 0 auto;
    padding: 0.5rem 1.5rem 2rem;
  }
  .overview {
    border-top: 1px solid var(--border);
    padding: 1.25rem 0 0;
  }
  .block h2,
  .audience-col h2 {
    margin: 0 0 0.65rem;
    font-size: 1.35rem;
    letter-spacing: -0.02em;
  }
  .prose, .lede, .audience-col p, .principle p, .defs dd {
    color: var(--text-muted);
  }
  .prose {
    margin: 0 0 0.85rem;
    max-width: 42rem;
  }
  .prose :global(strong) { color: var(--text); font-weight: 650; }
  .more {
    margin: 0 0 1.6rem;
    font-size: 0.9rem;
  }
  .more a {
    color: var(--text);
    text-underline-offset: 0.18em;
  }
  .block {
    padding: 2.75rem 0 0;
    border-top: 1px solid var(--border);
  }
  .overview .block:first-of-type { border-top: none; padding-top: 1.5rem; }
  .lede { margin: 0 0 1.75rem; max-width: 42rem; }
  .principles {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2rem 2.25rem;
  }
  .num {
    font-size: 1.65rem;
    font-weight: 700;
    letter-spacing: -0.04em;
    line-height: 1;
    margin-bottom: 0.45rem;
  }
  .principle h3, .audience-col h3, .defs dt {
    margin: 0 0 0.35rem;
    font-size: 1.02rem;
  }
  .principle p { margin: 0; }

  .defs {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.5rem 2.25rem;
    margin: 0;
  }
  .defs dt { font-weight: 650; }
  .defs dd { margin: 0; font-size: 0.95rem; }

  .audience {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2.5rem 3rem;
    padding: 2.75rem 0 0;
    border-top: 1px solid var(--border);
  }
  .audience-col h3 { margin-top: 1.35rem; }
  .audience-col h3:first-of-type { margin-top: 0.15rem; }
  .audience-col p { margin: 0; }
  .kicker {
    margin: 0.9rem 0 0.35rem !important;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-faint);
  }
  .plain {
    list-style: none;
    margin: 0.55rem 0 0;
    padding: 0;
  }
  .plain li {
    position: relative;
    padding-left: 0.9rem;
    margin: 0.3rem 0;
    color: var(--text-muted);
    font-size: 0.9rem;
  }
  .plain li::before {
    content: "";
    position: absolute;
    left: 0;
    top: 0.55em;
    width: 0.35rem;
    height: 1px;
    background: var(--text-faint);
  }

  .start-row {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 1.35rem 1.75rem;
  }
  .start-row a {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    text-decoration: none;
    color: inherit;
  }
  .start-row a:hover strong { text-decoration: underline; text-underline-offset: 0.16em; }
  .start-row strong { font-size: 1.02rem; }
  .start-row span { color: var(--text-muted); font-size: 0.9rem; }
  .invite {
    margin: 1.5rem 0 0;
    font-size: 0.9rem;
  }
  .invite a {
    color: var(--text);
    text-underline-offset: 0.18em;
  }

  @media (max-width: 760px) {
    .about-hero { padding: 4rem 1.25rem 2rem; }
    .principles, .defs, .audience, .start-row { grid-template-columns: 1fr; }
    .audience { gap: 2.25rem; }
  }
</style>
