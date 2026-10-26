<script>
  import { Button, Spinner } from 'flowbite-svelte';
  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import { get } from 'svelte/store';
  import { principal, isAuthenticated } from '$lib/stores/auth';
  import { login, logout, restoreAuthSession, resetAuthSessionRestore } from '$lib/auth';
  import { isEmbeddedInPortal, portalNavPush } from '$lib/portal-bridge.ts';
  import { consumePortalSigningOut, consumePortalSigningIn } from '$lib/portal-redirect-path.ts';
  import { backend, backendReady, initBackendWithIdentity, setActiveQuarter, createQuarterActor } from '$lib/canisters.js';
  import { must, on, read } from '$lib/realm';
  import { loadUserProfiles, profilesLoading } from '$lib/stores/profiles';
  import { activeQuarterId } from '$lib/stores/quarters';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { resolve } from '$app/paths';
  import { realmInfo, realmName as realmNameStore, realmWelcomeMessage, realmManifesto, realmOpenRegistration, realmPrimaryLanguage, testMode, testModeIIBypass, testModeUserSelfRegistration, testModeSkipTerms, testModeDemoNotice, demoNoticeBody } from '$lib/stores/realmInfo';
  import DemoNotice from '$lib/components/DemoNotice.svelte';
  import { shouldShowJoinNotice } from '$lib/config/hostTestFlags';
  import { cn } from '$lib/theme/utilities';
  import { formatQuarterLabel } from '$lib/utils/quarterLabels';
  import { probeFederatedMembership, activateMembership } from '$lib/utils/federatedMembership';
  import {
    listTestIdentities,
    shortPrincipal,
    getTestIdentityPersona,
    identityNumberToIndex,
    isValidCustomIdentityNumber,
    testIdentityLabel,
    testIdentityNumber,
    TEST_IDENTITY_FIXED_PICKER_MAX_INDEX,
    TEST_IDENTITY_MAX_INDEX,
    normalizeTestIdentityIndex,
  } from '$lib/test-identities.js';
  import { parseTestIdentitySearch, applyTestIdentitySearch } from '$lib/test-identity-query.ts';
  import { _ } from 'svelte-i18n';
  import { isAssignableJoinError, localizeBackendError } from '$lib/utils/backendError';

  function t(key, values) {
    return get(_)(key, values);
  }
  
  // Step management: 'auth' | 'already_joined' | 'terms' | 'profile' | 'success'
  let currentStep = 'auth';
  let userHasJoined = false;
  let membershipProbed = false;
  /** @type {Promise<void> | null} */
  let membershipProbePromise = null;
  /** @type {import('$lib/utils/federatedMembership').MembershipHit[]} */
  let membershipHits = [];
  let probeCapitalId = '';
  let selectedActivationId = '';
  let agreement = false;
  let error = '';
  let loading = false;
  // Returning from the portal, the session arrives a moment after this page
  // paints. Hold the sign-in form until that handoff settles.
  let awaitingPortalSession = browser && isEmbeddedInPortal();
  let portalHandoffIsSignOut = browser && consumePortalSigningOut();
  // Back from the portal login: the portal may still be restoring its session
  // and answer "no session" first, so only the delegation (or an error) ends
  // the wait.
  let portalHandoffIsSignIn = browser && consumePortalSigningIn() && !portalHandoffIsSignOut;

  function releasePortalHandoff() {
    awaitingPortalSession = false;
  }
  // Signed in, but membership is still being probed: keep the spinner up so
  // the sign-in form does not flash before the next step.
  $: finishingSignIn = currentStep === 'auth' && $isAuthenticated && !error;
  $: realmName = $realmNameStore || 'Realm';
  let inviteCode = '';
  let inviteProfile = '';
  let inviteValid = false;
  let inviteError = '';
  let inviteChecking = false;

  // Quarter targeting (issue #156): a new member joins exactly ONE quarter.
  // Target comes from invite (?quarter=) or system assignment via
  // join_targets.default (least-populated joinable). No free picker.
  let capitalId = '';
  let joinMode = 'auto';        // 'auto' | 'choice' (choice must not enable a free picker)
  let quarterDirectory = [];    // all quarters from quarter_directory (for lookups)
  let selectedQuarter = '';     // canister id assigned / invite override
  let targetQuarterId = '';     // resolved join target
  let targetActor = null;       // actor for the target quarter (or capital backend)
  let targetsResolved = false;
  let forgotLoading = false;
  let embeddedInPortal = false;
  let forgotError = '';
  /** @type {ReturnType<typeof listTestIdentities>} */
  let testIdentities = listTestIdentities();
  function readSelectedTestIdentityIndex() {
    if (!__REALMS_TEST_BUILD__) return 0;
    if (typeof window === 'undefined') return 0;
    const parsed = parseTestIdentitySearch(window.location.search);
    return parsed.identityIndex ?? 0;
  }
  let selectedTestIdentityIndex = readSelectedTestIdentityIndex();
  let customIdentityNumber = selectedTestIdentityIndex > TEST_IDENTITY_FIXED_PICKER_MAX_INDEX
    ? testIdentityNumber(selectedTestIdentityIndex)
    : 3;
  /** Quarters skipped this session after assignable join errors (e.g. still bootstrapping). */
  let skippedJoinQuarters = [];

  $: customIdentityIndex = isValidCustomIdentityNumber(customIdentityNumber)
    ? identityNumberToIndex(customIdentityNumber)
    : null;
  $: customPersona = customIdentityIndex != null ? getTestIdentityPersona(customIdentityIndex) : null;
  $: maxCustomIdentityNumber = testIdentityNumber(TEST_IDENTITY_MAX_INDEX);
  $: selectedIdentityLabel = testIdentityLabel(selectedTestIdentityIndex);
  // Host nav:sync may add `?ti=` after mount without remounting this page.
  $: if (__REALMS_TEST_BUILD__) {
    const parsed = parseTestIdentitySearch($page.url.search);
    if (parsed.identityIndex != null && parsed.identityIndex !== selectedTestIdentityIndex) {
      selectedTestIdentityIndex = parsed.identityIndex;
      if (selectedTestIdentityIndex > TEST_IDENTITY_FIXED_PICKER_MAX_INDEX) {
        customIdentityNumber = testIdentityNumber(selectedTestIdentityIndex);
      }
    }
  }
  // The granted profile is resolved by the backend (issue #242): the invite
  // code's profile when a code is used, otherwise the codex-defined default.
  // There is no profile picker — user types are gone; only profiles exist.

  // Invite is required when registration is closed (not open) and user has no valid invite.
  // II bypass replaces Internet Identity only — incumbent realms still need a registration code.
  $: inviteRequired = !$realmOpenRegistration && !inviteValid && !$testModeUserSelfRegistration;

  $: targetQuarterInfo = quarterDirectory.find((q) => q.canister_id === targetQuarterId) || null;
  // Assignment banner when federation has multiple quarters or target is a sub-quarter.
  // Test mode adds a quarter picker so staging can register on any quarter.
  $: showQuarterBanner = !!targetQuarterId && (
    $testMode ||
    quarterDirectory.length > 1 ||
    (!!capitalId && targetQuarterId !== capitalId)
  );
  $: showTestModeQuarterPicker = $testMode && quarterDirectory.length > 0;

  $: welcomeImageUrl = $realmInfo.backgroundImageUrl || '/custom/background.png';

  const internetIdentityUrl =
    (typeof globalThis !== 'undefined' && globalThis.__CANISTER_IDS?.internet_identity) ||
    'https://identity.ic0.app';

  function openInternetIdentity() {
    window.open(internetIdentityUrl, '_blank', 'noopener,noreferrer');
  }

  $: showJoinNotice = shouldShowJoinNotice($testModeDemoNotice, $testModeSkipTerms);

  // ── Linear step model for the progress indicator (issue #156) ──────────────
  // Order: Sign In → Notice → Profile → Welcome. System assigns the quarter;
  // there is no free pick_quarter step on the open-registration path.
  $: steps = [
    { id: 'auth', labelKey: 'join.step_sign_in' },
    ...(showJoinNotice ? [{ id: 'terms', labelKey: 'join.step_notice' }] : []),
    { id: 'profile', labelKey: 'join.step_invitation' },
    { id: 'success', labelKey: 'join.step_welcome' },
  ];
  $: currentStepIndex = steps.findIndex((s) => s.id === currentStep);

  // The nearest earlier step the user is allowed to return to. Sign In is never
  // a back target once authenticated (you cannot un-authenticate by clicking,
  // and the reactive guard would bounce forward anyway), so we skip it. Null
  // means there is nowhere to go back to from the current step.
  $: prevStepId = (() => {
    for (let i = currentStepIndex - 1; i >= 0; i--) {
      const s = steps[i];
      if (s.id === 'auth' && $isAuthenticated) continue;
      return s.id;
    }
    return null;
  })();

  // Backward-only navigation from the stepper: a user may revisit any earlier
  // step, except Sign In once authenticated, and never while a join is in flight.
  function goToStep(stepId) {
    const idx = steps.findIndex((s) => s.id === stepId);
    if (idx < 0 || idx >= currentStepIndex) return;
    if (stepId === 'auth' && $isAuthenticated) return;
    if (loading) return;
    error = '';
    currentStep = stepId;
  }

  // Go to the previous navigable step (used by the explicit "Back" buttons).
  function goBack() {
    if (!prevStepId || loading) return;
    error = '';
    currentStep = prevStepId;
  }

  function stepAfterProbe() {
    // Founders registered at deploy time must not be sent back through invite.
    if (userHasJoined) return 'already_joined';
    // Demo notice replaces T&C. II bypass does not skip it.
    if (shouldShowJoinNotice($testModeDemoNotice, $testModeSkipTerms)) {
      return 'terms';
    }
    if ($testModeIIBypass || $testModeSkipTerms) {
      return 'profile';
    }
    return 'profile';
  }

  function membershipQuarterLabel(hit) {
    if (!hit) return '';
    const info = quarterDirectory.find((q) => q.canister_id === hit.canisterId);
    if (info) return formatQuarterLabel(info);
    return hit.canisterId || t('join.capital');
  }

  /** Federated membership probe before any new registration (issue #156). */
  async function runMembershipProbe() {
    if (membershipProbePromise) return membershipProbePromise;
    membershipProbePromise = (async () => {
      try {
        const { primary, hits, capitalId: probedCapital } = await probeFederatedMembership({
          activate: true,
          cache: true,
        });
        userHasJoined = !!primary;
        membershipHits = hits || [];
        probeCapitalId = probedCapital || capitalId || '';
        selectedActivationId = primary?.canisterId || membershipHits[0]?.canisterId || '';
      } catch (e) {
        console.warn('Federated membership probe failed; falling back to target check', e);
        userHasJoined = await isJoinedOnTarget();
        membershipHits = [];
        probeCapitalId = capitalId || '';
        selectedActivationId = '';
      } finally {
        membershipProbed = true;
        membershipProbePromise = null;
      }
    })();
    return membershipProbePromise;
  }

  /** Activate the selected membership for this session, then enter the app. */
  async function continueWithMembership() {
    error = '';
    const hit =
      membershipHits.find((h) => h.canisterId === selectedActivationId) || membershipHits[0];
    if (!hit) {
      await goto(resolve('/extensions/member_dashboard'));
      return;
    }
    loading = true;
    try {
      await activateMembership(hit, probeCapitalId || capitalId, { cache: true });
      await goto(resolve('/extensions/member_dashboard'));
    } catch (e) {
      console.error('Failed to activate membership', e);
      error = e.message || t('join.activate_failed');
    } finally {
      loading = false;
    }
  }

  /** Probe (if needed) then leave the auth step — never skip the federated probe. */
  async function ensureProbedAndAdvance() {
    if (currentStep !== 'auth') return;
    await runMembershipProbe();
    if (currentStep === 'auth') {
      currentStep = stepAfterProbe();
    }
  }
  
  async function advanceStepAfterAuth(identity) {
    await initBackendWithIdentity(identity);
    // The target actor may have been built before authentication completed
    // (resolveJoinTarget runs on mount) — an anonymous actor here makes the
    // membership probe and join_realm run as the anonymous principal 2vxsx-fae.
    // Rebuild it now that an identity is available.
    if (targetQuarterId) {
      await selectQuarter(targetQuarterId, identity);
    }
    await loadUserProfiles();
    if (inviteCode) {
      await validateInvite();
    }
    // Always probe federation before deciding Terms/Profile vs welcome-back.
    // II bypass only replaces Internet Identity — not membership detection.
    await ensureProbedAndAdvance();
  }

  onMount(() => {
    let onPortalAuth;
    let onPortalPending;
    let onPortalAuthError;
    let handoffTimer;
    let disposed = false;
    // The portal answers the first probe during hydration, before the awaits
    // below finish. Listen now so a "no session" reply is not missed.
    if (awaitingPortalSession) {
      onPortalPending = () => {
        if (!portalHandoffIsSignIn) releasePortalHandoff();
      };
      window.addEventListener('portal:auth-pending', onPortalPending);
      handoffTimer = setTimeout(releasePortalHandoff, portalHandoffIsSignIn ? 20000 : 8000);
    }
    let authUnsub = () => {};
    let profilesUnsub = () => {};

    // Session/portal races: if auth becomes ready while still on the auth step,
    // run the federated probe before Terms/Profile (not a reactive $: side-effect).
    const maybeAdvanceFromStores = () => {
      if (disposed || !targetsResolved) return;
      if (!get(isAuthenticated) || get(profilesLoading)) return;
      if (currentStep !== 'auth' || membershipProbed) return;
      void ensureProbedAndAdvance();
    };
    authUnsub = isAuthenticated.subscribe(() => maybeAdvanceFromStores());
    profilesUnsub = profilesLoading.subscribe(() => maybeAdvanceFromStores());

    void (async () => {
      await backendReady;
      if (disposed) return;
      embeddedInPortal = isEmbeddedInPortal();

      // Hydrate stores from AuthClient before step logic (portal iframes remount often).
      await restoreAuthSession();
      if (disposed) return;

      const urlParams = new URLSearchParams(window.location.search);
      inviteCode = urlParams.get('invite') || urlParams.get('code') || '';
      const quarterParam = urlParams.get('quarter') || '';
      const fromQuery = parseTestIdentitySearch(urlParams);
      if (__REALMS_TEST_BUILD__ && fromQuery.identityIndex != null) {
        persistSelectedTestIdentity(fromQuery.identityIndex);
      }

      await resolveJoinTarget(quarterParam);
      if (disposed) return;

      await realmInfo.fetch();
      if (disposed) return;

      if ($testModeIIBypass) {
        releasePortalHandoff();
        await logout();
        isAuthenticated.set(false);
        principal.set('');
        membershipProbed = false;
        userHasJoined = false;
        membershipHits = [];
        probeCapitalId = '';
        selectedActivationId = '';
        currentStep = 'auth';
      }

      if ($isAuthenticated) {
        try {
          await advanceStepAfterAuth();
        } catch (e) {
          console.error('Restoring the session failed:', e);
          error = t('join.auth_failed');
        }
      }

      targetsResolved = true;
      maybeAdvanceFromStores();

      if (embeddedInPortal && !$testModeIIBypass) {
        onPortalAuth = () => {
          if ($isAuthenticated) return;
          void handleLogin();
        };
        onPortalAuthError = (event) => {
          loading = false;
          releasePortalHandoff();
          console.warn('[portal] delegation unavailable:', event?.detail?.error);
        };
        window.addEventListener('portal:auth', onPortalAuth);
        window.addEventListener('portal:auth-error', onPortalAuthError);
      } else {
        releasePortalHandoff();
      }
    })();

    return () => {
      disposed = true;
      if (handoffTimer) clearTimeout(handoffTimer);
      if (onPortalPending) window.removeEventListener('portal:auth-pending', onPortalPending);
      authUnsub();
      profilesUnsub();
      if (onPortalAuth) window.removeEventListener('portal:auth', onPortalAuth);
      if (onPortalAuthError) window.removeEventListener('portal:auth-error', onPortalAuthError);
    };
  });

  async function loadJoinPolicy() {
    const [targets, directory] = await Promise.all([must(read('join_targets')), must(read('quarter_directory'))]);
    return {
      capital_id: directory.capital,
      mode: targets.mode,
      default_quarter: targets.default,
      quarters: directory.quarters.map((q) => ({
        canister_id: q.id,
        name: q.name,
        population: q.population,
        status: q.status,
        index: q.index,
        is_capital: q.capital,
        joinable: targets.targets.includes(q.id),
      })),
    };
  }

  // Ask the capital where new members may register and assign a target quarter.
  async function resolveJoinTarget(quarterParam) {
    let policy = null;
    try {
      policy = await loadJoinPolicy();
    } catch (e) {
      console.warn('join_targets failed; defaulting to capital', e);
    }

    capitalId = policy?.capital_id || '';
    joinMode = policy?.mode || 'auto';
    quarterDirectory = policy?.quarters || [];
    // Product path: never open a free picker for open registration, even when
    // the backend still reports mode === 'choice'.

    // Invite links target the quarter encoded in the link, regardless of mode.
    if (quarterParam) {
      await selectQuarter(quarterParam);
      return;
    }

    const def = policy?.default_quarter || capitalId || '';
    selectedQuarter = def;
    // System-assigned least-populated joinable (or capital fallback).
    await selectQuarter(def);
  }

  function quarterOptionSuffix(quarter) {
    if (quarter.joinable !== false) return '';
    if (!quarter.is_capital && quarter.status === 'setup') return t('join.quarter_setting_up');
    return t('join.quarter_coordinator_only');
  }

  /** Pick least-populated joinable quarter, excluding skipped ids (issue #156). */
  function pickJoinQuarterExcluding(directory, skipped) {
    const skip = new Set(skipped || []);
    const joinable = (directory || []).filter(
      (q) => q.joinable !== false && q.canister_id && !skip.has(q.canister_id),
    );
    const subs = joinable.filter((q) => !q.is_capital);
    if (subs.length === 0) {
      const cap = joinable.find((q) => q.is_capital);
      return cap?.canister_id || '';
    }
    return subs.reduce((best, q) => {
      const pop = Number(q.population) || 0;
      const idx = Number(q.index) || 0;
      if (!best) return q;
      const bestPop = Number(best.population) || 0;
      const bestIdx = Number(best.index) || 0;
      if (pop < bestPop || (pop === bestPop && idx > bestIdx)) return q;
      return best;
    }, null)?.canister_id || '';
  }

  /** Re-fetch join targets and retarget after coordinator-only / full errors. */
  async function reassignJoinTarget() {
    try {
      const policy = await loadJoinPolicy();
      capitalId = policy.capital_id || capitalId;
      joinMode = policy.mode || joinMode;
      quarterDirectory = policy.quarters;
      const def = pickJoinQuarterExcluding(quarterDirectory, skippedJoinQuarters);
      if (def && def !== targetQuarterId) {
        await selectQuarter(def);
      }
    } catch (e) {
      console.warn('Failed to re-resolve join target after join error', e);
    }
  }

  // Point the page at a specific quarter (or the capital) for validate + join.
  async function selectQuarter(qid, explicitIdentity = null) {
    selectedQuarter = qid;
    targetQuarterId = qid;
    if (!qid || qid === capitalId) {
      targetActor = backend; // capital (single-realm or coordinator fallback)
      return;
    }
    try {
      targetActor = await createQuarterActor(qid, explicitIdentity);
    } catch (e) {
      console.error('Failed to build quarter actor, falling back to capital:', e);
      targetActor = backend;
    }
  }

  /** Test mode only: let the user pick any quarter before validate + join. */
  async function handleTestModeQuarterChange(event) {
    const qid = event.currentTarget.value;
    if (!qid || qid === targetQuarterId) return;
    const revalidateInvite = inviteValid && !!inviteCode;
    if (revalidateInvite) {
      inviteValid = false;
      inviteProfile = '';
      inviteError = '';
    }
    await selectQuarter(qid);
    if (revalidateInvite) {
      await validateInvite();
    }
  }

  function onTarget(method, name, args = {}) {
    return on(targetActor || backend)[method](name, args);
  }

  // Fallback: is the authenticated caller already a member on the resolved target?
  async function isJoinedOnTarget() {
    try {
      const res = await onTarget('read', 'me');
      return !!(res.ok && res.user.status);
    } catch (e) {
      return false;
    }
  }

  // "Find my quarter" — federated probe, then enter the app (or report miss).
  async function findMyQuarter() {
    forgotError = '';
    forgotLoading = true;
    try {
      if (!$isAuthenticated) {
        await handleLogin();
      }
      if (!$isAuthenticated) {
        forgotError = t('join.sign_in_to_find');
        return;
      }

      const { primary } = await probeFederatedMembership({ activate: true, cache: true });
      if (primary) {
        userHasJoined = true;
        membershipProbed = true;
        await goto(resolve('/'));
        return;
      }
      forgotError = t('join.membership_not_found');
    } catch (e) {
      console.warn('Find my quarter failed', e);
      forgotError = t('join.membership_not_found');
    } finally {
      forgotLoading = false;
    }
  }

  function persistSelectedTestIdentity(index) {
    if (!__REALMS_TEST_BUILD__) return;
    selectedTestIdentityIndex = normalizeTestIdentityIndex(index);
    if (selectedTestIdentityIndex > TEST_IDENTITY_FIXED_PICKER_MAX_INDEX) {
      customIdentityNumber = testIdentityNumber(selectedTestIdentityIndex);
    }
    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    const nextSearch = applyTestIdentitySearch(url.search, {
      identityIndex: selectedTestIdentityIndex,
    });
    const qs = nextSearch ? `?${nextSearch}` : '';
    const nextPath = `${url.pathname}${qs}${url.hash}`;
    if (`${url.pathname}${url.search}${url.hash}` === nextPath) return;
    history.replaceState(history.state, '', nextPath);
    if (isEmbeddedInPortal()) {
      const share = new URLSearchParams(nextSearch);
      share.delete('portal');
      share.delete('slug');
      const shareQs = share.toString();
      portalNavPush(`${url.pathname}${shareQs ? `?${shareQs}` : ''}${url.hash}`, { replace: true });
    }
  }

  async function continueAsSelectedTestIdentity() {
    if (!__REALMS_TEST_BUILD__) return;
    persistSelectedTestIdentity(selectedTestIdentityIndex);
    await handleLogin({
      identityIndex: selectedTestIdentityIndex,
      preferTestMode: true,
    });
  }

  async function completeAuthAfterLogin(userPrincipal, identity) {
    isAuthenticated.set(true);
    principal.set(userPrincipal.toText());
    await advanceStepAfterAuth(identity);
  }

  async function handleLogin(options = {}) {
    loading = true;
    error = '';
    try {
      const preferTestMode = options.preferTestMode ?? get(testModeIIBypass);
      const { principal: userPrincipal, identity } = await login({ ...options, preferTestMode });
      if (userPrincipal && identity) {
        await completeAuthAfterLogin(userPrincipal, identity);
      } else {
        error = t('join.login_cancelled');
      }
    } catch (e) {
      console.error('Login error:', e);
      error = t('join.auth_failed');
    } finally {
      loading = false;
    }
  }

  function validateInvite() {
    if (!inviteCode) return;
    inviteError = '';
    error = '';
    // Green-field test realms only: magic invite strings accepted client-side.
    const trimmed = inviteCode.trim().toLowerCase();
    if ($testModeUserSelfRegistration && (trimmed === 'admin' || trimmed === 'member' || trimmed === 'dev' || trimmed === 'developer')) {
      inviteProfile = trimmed === 'dev' || trimmed === 'developer' ? 'developer' : trimmed;
    }
    // The realm checks a code, and tells its profile, only when realm_join redeems it.
    inviteValid = true;
  }

  function handleTermsAccept() {
    if (!agreement) {
      error = t('join.confirm_notice');
      return;
    }
    error = '';
    currentStep = 'profile';
  }

  async function handleJoin() {
    error = '';

    if (inviteRequired) {
      error = t('errors.invite_required');
      return;
    }

    try {
      loading = true;
      console.log(`Joining quarter ${targetQuarterId || '(capital)'}${inviteProfile ? ` as ${inviteProfile}` : ''}`);
      // Register directly on the resolved target quarter (single call). The
      // invite code is consumed on that quarter, where it lives. The profile
      // is resolved server-side (invite code profile or codex default).
      // A known profile means a client-side test code, which is not a realm invite.
      const response = await onTarget('run', 'realm_join', { code: inviteProfile ? '' : inviteCode });
      if (response.ok) {
        // Point the app at the quarter we just joined and remember it.
        if (targetQuarterId && targetQuarterId !== capitalId) {
          activeQuarterId.set(targetQuarterId);
          await setActiveQuarter(targetQuarterId);
          if (typeof localStorage !== 'undefined') {
            localStorage.setItem('home_quarter', targetQuarterId);
          }
        } else {
          activeQuarterId.set(null);
          await setActiveQuarter(null);
          if (typeof localStorage !== 'undefined') {
            localStorage.removeItem('home_quarter');
          }
        }
        // The profiles store still holds the pre-join answer ("no profiles" →
        // Guest) and the memoized session restore would keep serving it to the
        // dashboard after navigation. Refresh both now that we're a member.
        resetAuthSessionRestore();
        await loadUserProfiles();
        currentStep = 'success';
      } else {
        const joinError = response.error || t('errors.generic');
        error = localizeBackendError(joinError, (key) => t(key));
        // Coordinator-only / full / bootstrapping: re-resolve assignment, no free picker.
        if (isAssignableJoinError(joinError)) {
          if (targetQuarterId && !skippedJoinQuarters.includes(targetQuarterId)) {
            skippedJoinQuarters = [...skippedJoinQuarters, targetQuarterId];
          }
          await reassignJoinTarget();
        }
      }
    } catch (e) {
      console.error('Error joining realm:', e);
      error = localizeBackendError(e.message || t('join.failed_join'), (key) => t(key));
      if (isAssignableJoinError(error)) {
        if (targetQuarterId && !skippedJoinQuarters.includes(targetQuarterId)) {
          skippedJoinQuarters = [...skippedJoinQuarters, targetQuarterId];
        }
        await reassignJoinTarget();
      }
    } finally {
      loading = false;
    }
  }
</script>

<div class="min-h-viewport md:h-viewport grid grid-cols-1 md:grid-cols-2" style="transform: none !important;">
  <!-- Left Brand Panel with Background Image -->
  <div class="hidden md:flex md:col-start-1 text-white flex-col justify-between relative">
    <!-- Background Image - no overlay, full opacity -->
    <div class="absolute inset-0 z-0">
      <img 
        src={welcomeImageUrl} 
        alt={t('join.background_alt', { values: { name: realmName } })}
        class="w-full h-full object-cover"
      />
    </div>
    
    <!-- Content overlay - centered vertically -->
    <div class="relative z-10 p-6 flex flex-col h-full justify-center items-center">
      <!-- Text container with dark background for readability -->
      <div class="bg-black/60 backdrop-blur-sm rounded-2xl p-6 max-w-md mx-6">
        <!-- Welcome Message -->
        <h1 class="text-4xl font-bold mb-4 leading-tight">
          {$_('join.welcome_to', { values: { name: realmName } })}
        </h1>
        
        {#if $realmWelcomeMessage}
          <p class="text-lg text-white/90 mb-4">
            {$realmWelcomeMessage}
          </p>
        {/if}
        
        {#if $realmManifesto}
          <p class="text-lg text-white/90">
            {$realmManifesto}
          </p>
        {/if}
      </div>
    </div>
  </div>

  <!-- Right Form Panel -->
  <div class="flex items-start md:items-center justify-center p-4 pb-16 md:p-12 relative bg-gradient-to-br from-gray-50 to-gray-100 md:col-start-2 overflow-y-auto">
    <!-- Mobile background image -->
    <div class="md:hidden absolute inset-0 z-0">
      <img 
        src={welcomeImageUrl} 
        alt={t('join.background_alt', { values: { name: realmName } })}
        class="w-full h-full object-cover"
      />
    </div>
    
    <div class="w-full max-w-md relative z-10 md:bg-transparent md:backdrop-blur-none md:rounded-none md:p-0 bg-white/80 backdrop-blur-sm rounded-2xl p-3 my-auto">

      <!-- Step Indicator (dynamic; backward-clickable). Labels sit under each
           dot so they never crowd or wrap, and the connectors flex to spread
           the steps evenly across the full width. -->
      {#if currentStep !== 'already_joined'}
        <div class="flex items-start justify-between mb-6 md:mb-8 px-1">
          {#each steps as step, i (step.id)}
            {#if i > 0}
              <div class="flex-1 h-px bg-gray-300 mt-3.5 sm:mt-4 mx-1.5"></div>
            {/if}
            {@const isCurrent = i === currentStepIndex}
            {@const isDone = currentStepIndex >= 0 && i < currentStepIndex}
            {@const clickable = isDone && !(step.id === 'auth' && $isAuthenticated) && !loading}
            <button
              type="button"
              on:click={() => goToStep(step.id)}
              disabled={!clickable}
              aria-current={isCurrent ? 'step' : undefined}
              class={cn('flex flex-col items-center gap-1.5 shrink-0 transition-all', clickable ? 'cursor-pointer group' : 'cursor-default')}
            >
              <div class={cn(
                "w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-xs sm:text-sm font-medium transition-all",
                isCurrent ? "bg-gray-900 text-white"
                  : isDone ? "bg-gray-700 text-white group-hover:bg-gray-900"
                  : "bg-gray-200 text-gray-500"
              )}>
                {#if isDone}✓{:else}{i + 1}{/if}
              </div>
              <span class={cn(
                "text-[11px] sm:text-xs leading-none whitespace-nowrap transition-colors",
                isCurrent ? "text-gray-900 font-medium" : "text-gray-500",
                clickable && "group-hover:text-gray-900"
              )}>{$_(step.labelKey)}</span>
            </button>
          {/each}
        </div>
      {/if}

      <!-- Error Display -->
      {#if error}
        <div class="mb-6 p-4 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
          {error}
        </div>
      {/if}

      {#if inviteChecking}
        <div class="mb-6 p-4 bg-blue-50 border border-blue-200 rounded-xl text-blue-700 text-sm flex items-center gap-2">
          <Spinner size="4" color="blue" />
          <span>{$_('join.validating_invite')}</span>
        </div>
      {/if}

      <!-- Step: Auth -->
      {#if currentStep === 'auth' && (awaitingPortalSession || finishingSignIn)}
        <div class="bg-white rounded-2xl shadow-xl p-5 md:p-8 border border-gray-100" role="status" aria-live="polite">
          <div class="text-center py-6">
            <div class="flex justify-center mb-6">
              <Spinner size="8" color="gray" />
            </div>
            <h2 class="text-2xl font-bold text-gray-900 mb-2">
              {portalHandoffIsSignOut && !finishingSignIn ? $_('join.signing_out') : $_('join.signing_in')}
            </h2>
            <p class="text-gray-500">{$_('join.signing_in_hint')}</p>
          </div>
        </div>
      {:else if currentStep === 'auth'}
        <div class="bg-white rounded-2xl shadow-xl p-5 md:p-8 border border-gray-100">
          <div class="text-center mb-8">
            <div class="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg class="w-8 h-8 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
            </div>
            <h2 class="text-2xl font-bold text-gray-900 mb-2">{$_('join.sign_in_title')}</h2>
            <p class="text-gray-500">
              {#if __REALMS_TEST_BUILD__ && $testModeIIBypass}
                {$_('join.sign_in_test', { values: { name: realmName } })}
              {:else}
                {$_('join.sign_in_ii', { values: { name: realmName } })}
              {/if}
            </p>
          </div>

          {#if __REALMS_TEST_BUILD__ && $testModeIIBypass}
            <div class="space-y-3">
              <p class="text-sm text-gray-600 text-center mb-2">
                {$_('join.test_identity_hint')}
              </p>
              {#each testIdentities as persona (persona.index)}
                <button
                  type="button"
                  on:click={() => persistSelectedTestIdentity(persona.index)}
                  class={cn(
                    'w-full text-left p-4 rounded-xl border-2 transition-all',
                    selectedTestIdentityIndex === persona.index
                      ? 'border-gray-900 bg-gray-50'
                      : 'border-gray-200 hover:border-gray-300 bg-white'
                  )}
                  disabled={loading}
                >
                  <div class="flex items-center justify-between gap-3">
                    <span class="font-semibold text-gray-900">{persona.label}</span>
                    {#if selectedTestIdentityIndex === persona.index}
                      <span class="text-xs font-medium text-gray-700 bg-gray-200 px-2 py-0.5 rounded-full">{$_('join.selected')}</span>
                    {/if}
                  </div>
                  <p class="text-xs font-mono text-gray-500 mt-1 break-all">{persona.principal}</p>
                  {#if persona.registeredFounder && persona.registeredFounder !== persona.principal}
                    <p class="text-xs text-amber-700 mt-1">
                      {$_('join.registered_founder', { values: { principal: shortPrincipal(persona.registeredFounder) } })}
                    </p>
                  {/if}
                  <p class="text-xs text-gray-400 mt-1">{persona.description}</p>
                </button>
              {/each}

              <div
                class={cn(
                  'w-full p-4 rounded-xl border-2 transition-all',
                  customIdentityIndex != null && selectedTestIdentityIndex === customIdentityIndex
                    ? 'border-gray-900 bg-gray-50'
                    : 'border-gray-200 bg-white'
                )}
              >
                <label class="block text-sm font-semibold text-gray-900 mb-2" for="join-custom-identity-number">
                  {$_('join.other_identity')}
                </label>
                <div class="flex gap-2">
                  <input
                    id="join-custom-identity-number"
                    class="flex-1 min-w-0 px-3 py-2 border border-gray-300 rounded-lg text-sm"
                    type="number"
                    min="3"
                    max={maxCustomIdentityNumber}
                    step="1"
                    inputmode="numeric"
                    bind:value={customIdentityNumber}
                    disabled={loading}
                    on:change={() => {
                      if (customIdentityIndex != null) persistSelectedTestIdentity(customIdentityIndex);
                    }}
                  />
                  <button
                    type="button"
                    class="px-3 py-2 text-sm font-medium border border-gray-900 rounded-lg hover:bg-gray-50 disabled:opacity-50"
                    disabled={loading || customIdentityIndex == null}
                    on:click={() => {
                      if (customIdentityIndex != null) persistSelectedTestIdentity(customIdentityIndex);
                    }}
                  >
                    {$_('join.select')}
                  </button>
                </div>
                {#if customPersona}
                  <p class="text-xs font-mono text-gray-500 mt-2 break-all">{customPersona.principal}</p>
                  <p class="text-xs text-gray-400 mt-1">{customPersona.description}</p>
                {:else}
                  <p class="text-xs text-gray-400 mt-2">
                    {$_('join.identity_numbers', { values: { max: maxCustomIdentityNumber.toLocaleString() } })}
                  </p>
                {/if}
              </div>
              <button
                type="button"
                on:click={continueAsSelectedTestIdentity}
                disabled={loading}
                class="w-full py-4 px-6 bg-gray-900 hover:bg-gray-800 text-white font-medium rounded-xl transition-all flex items-center justify-center gap-3 disabled:opacity-50 disabled:cursor-not-allowed mt-2"
              >
                {#if loading}
                  <Spinner size="5" color="white" />
                  <span>{$_('join.connecting')}</span>
                {:else}
                  <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                  </svg>
                  <span>{$_('join.continue_as', { values: { label: selectedIdentityLabel } })}</span>
                {/if}
              </button>
            </div>
          {:else}
            <button
              on:click={() => handleLogin()}
              disabled={loading}
              class="w-full py-4 px-6 bg-gray-900 hover:bg-gray-800 text-white font-medium rounded-xl transition-all flex items-center justify-center gap-3 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {#if loading}
                <Spinner size="5" color="white" />
                <span>{$_('join.connecting')}</span>
              {:else}
                <svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
                </svg>
                <span>{$_('join.sign_in_ii_button')}</span>
              {/if}
            </button>

            <p class="mt-6 text-center text-sm text-gray-500">
              {$_('join.no_ii')}
              <button
                type="button"
                on:click={openInternetIdentity}
                class="text-gray-700 hover:text-gray-900 hover:underline font-medium"
              >
                {$_('join.create_ii')}
              </button>
            </p>
          {/if}

          <!-- Returning member who forgot their quarter -->
          <div class="mt-6 pt-5 border-t border-gray-100 text-center">
            <p class="text-sm text-gray-500 mb-2">{$_('join.already_member_quarter')}</p>
            <button
              type="button"
              on:click={findMyQuarter}
              disabled={forgotLoading}
              class="text-sm font-medium text-gray-700 hover:text-gray-900 hover:underline disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
            >
              {#if forgotLoading}
                <Spinner size="4" color="gray" />
                <span>{$_('join.searching_quarters')}</span>
              {:else}
                <span>{$_('join.find_quarter')}</span>
              {/if}
            </button>
            {#if forgotError}
              <p class="mt-2 text-sm text-red-600">{forgotError}</p>
            {/if}
          </div>
        </div>

      <!-- Step: Already Joined (Welcome Back / multi-membership activation) -->
      {:else if currentStep === 'already_joined'}
        <div class="bg-white rounded-2xl shadow-xl p-5 md:p-8 border border-gray-100">
          <div class="text-center mb-8">
            <div class="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg class="w-8 h-8 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h2 class="text-2xl font-bold text-gray-900 mb-2">{$_('join.welcome_back')}</h2>
            <p class="text-gray-500">
              {$_('join.already_member_of', { values: { name: realmName } })}
            </p>
          </div>

          {#if membershipHits.length > 1}
            <p class="text-sm text-gray-500 mb-4 text-center">
              {$_('join.multiple_quarters')}
            </p>
            <div class="space-y-3 mb-6">
              {#each membershipHits as hit (hit.canisterId)}
                <button
                  type="button"
                  on:click={() => selectedActivationId = hit.canisterId}
                  class={cn(
                    "w-full p-4 rounded-xl border-2 text-left transition-all",
                    selectedActivationId === hit.canisterId
                      ? "border-gray-900 bg-gray-50"
                      : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"
                  )}
                >
                  <div class="font-semibold text-gray-900">{membershipQuarterLabel(hit)}</div>
                  {#if hit.profiles?.length}
                    <div class="text-sm text-gray-500 mt-0.5">{hit.profiles.join(', ')}</div>
                  {/if}
                </button>
              {/each}
            </div>
            <button
              type="button"
              on:click={continueWithMembership}
              disabled={!selectedActivationId || loading}
              class="w-full py-4 px-6 bg-gray-900 hover:bg-gray-800 text-white font-medium rounded-xl transition-all flex items-center justify-center gap-3 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {#if loading}
                <Spinner size="5" color="white" />
                <span>{$_('join.continuing')}</span>
              {:else}
                <span>{$_('setup.wizard.continue')}</span>
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" />
                </svg>
              {/if}
            </button>
          {:else}
            {#if membershipHits.length === 1}
              <p class="text-sm text-gray-500 mb-6 text-center">
                {$_('join.your_quarter', { values: { label: membershipQuarterLabel(membershipHits[0]) } })}
              </p>
            {/if}
            <a
              href={resolve('/extensions/member_dashboard')}
              class="w-full py-4 px-6 bg-gray-900 hover:bg-gray-800 text-white font-medium rounded-xl transition-all flex items-center justify-center gap-3"
            >
              <span>{$_('join.go_dashboard')}</span>
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" />
              </svg>
            </a>
          {/if}
        </div>

      <!-- Step: Terms -->
      {:else if currentStep === 'terms'}
        <div class="bg-white rounded-2xl shadow-xl p-5 md:p-8 border border-gray-100">
          {#if showQuarterBanner}
            <div class="mb-6 p-3 bg-gray-50 border border-gray-200 rounded-xl">
              <div class="text-xs uppercase tracking-wide text-gray-400">{$_('join.joining_quarter')}</div>
              {#if showTestModeQuarterPicker}
                <select
                  id="test-mode-quarter"
                  class="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-semibold text-gray-900 focus:border-gray-900 focus:ring-gray-900"
                  value={targetQuarterId}
                  on:change={handleTestModeQuarterChange}
                >
                  {#each quarterDirectory as quarter (quarter.canister_id)}
                    <option value={quarter.canister_id}>
                      {formatQuarterLabel(quarter)}{quarterOptionSuffix(quarter)}
                    </option>
                  {/each}
                </select>
                <p class="mt-2 text-xs text-gray-400">
                  {$_('join.test_mode_quarter')}
                </p>
              {:else}
                <div class="font-semibold text-gray-900 truncate">
                  {#if targetQuarterInfo}{formatQuarterLabel(targetQuarterInfo)}{:else}{targetQuarterId}{/if}
                </div>
              {/if}
            </div>
          {/if}
          
          <DemoNotice
            bodies={$demoNoticeBody}
            primaryLanguage={$realmPrimaryLanguage}
            bind:accepted={agreement}
          />
          
          <div class="flex gap-3">
            {#if prevStepId}
              <button
                on:click={goBack}
                class="flex-1 py-4 px-6 border border-gray-300 text-gray-700 font-medium rounded-xl hover:bg-gray-50 transition-all"
              >
                {$_('setup.wizard.back')}
              </button>
            {/if}
            <button
              on:click={handleTermsAccept}
              disabled={!agreement}
              class="flex-1 py-4 px-6 bg-gray-900 hover:bg-gray-800 text-white font-medium rounded-xl transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {$_('setup.wizard.continue')}
            </button>
          </div>
        </div>

      <!-- Step: Profile Selection -->
      {:else if currentStep === 'profile'}
        {#if inviteRequired}
          <div class="mb-6 p-4 bg-amber-50 border border-amber-200 rounded-xl text-amber-700 text-sm flex items-start gap-2">
            <svg class="w-5 h-5 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>{$_('errors.invite_required')}</span>
          </div>
        {/if}
        <div class="bg-white rounded-2xl shadow-xl p-5 md:p-8 border border-gray-100">
          <div class="flex items-center justify-between mb-2">
            <h2 class="text-2xl font-bold text-gray-900">{$_('join.join_title', { values: { name: realmName } })}</h2>
{#if inviteProfile}
              <span class="px-3 py-1 bg-green-100 text-green-700 text-xs font-medium rounded-full">{$_('join.invited_as', { values: { profile: inviteProfile } })}</span>
            {:else if $testMode}
              <span class="px-3 py-1 bg-gray-200 text-gray-600 text-xs font-medium rounded-full">{$_('join.test_mode')}</span>
            {/if}
          </div>
          <p class="text-gray-500 mb-6">
            {#if inviteValid}
              {$_('join.invite_determines_access')}
            {:else if $realmOpenRegistration}
              {$_('join.open_registration')}
            {:else}
              {$_('join.enter_invite_code')}
            {/if}
          </p>

          {#if showQuarterBanner}
            <div class="mb-6 p-3 bg-gray-50 border border-gray-200 rounded-xl">
              <div class="text-xs uppercase tracking-wide text-gray-400">{$_('join.joining_quarter')}</div>
              {#if showTestModeQuarterPicker}
                <select
                  id="test-mode-quarter"
                  class="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-semibold text-gray-900 focus:border-gray-900 focus:ring-gray-900"
                  value={targetQuarterId}
                  on:change={handleTestModeQuarterChange}
                >
                  {#each quarterDirectory as quarter (quarter.canister_id)}
                    <option value={quarter.canister_id}>
                      {formatQuarterLabel(quarter)}{quarterOptionSuffix(quarter)}
                    </option>
                  {/each}
                </select>
                <p class="mt-2 text-xs text-gray-400">
                  {$_('join.test_mode_quarter')}
                </p>
              {:else}
                <div class="font-semibold text-gray-900 truncate">
                  {#if targetQuarterInfo}{formatQuarterLabel(targetQuarterInfo)}{:else}{targetQuarterId}{/if}
                </div>
              {/if}
            </div>
          {/if}

          <!-- Access summary: only after a valid invite, or when open registration
               advertises the codex default profile. Invite-only realms stay code-only. -->
          {#if inviteProfile || $realmOpenRegistration}
          <div class="mb-6 p-4 rounded-xl border-2 border-gray-200 bg-gray-50">
            <div class="flex items-center gap-4">
              <div class="w-12 h-12 rounded-full flex items-center justify-center bg-gray-900 text-white">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
              </div>
              <div class="flex-1">
                {#if inviteProfile}
                  <div class="font-semibold text-gray-900 capitalize">{inviteProfile}</div>
                  <div class="text-sm text-gray-500">{$_('join.access_by_invite')}</div>
                {:else}
                  <div class="font-semibold text-gray-900">{$_('join.member_profile')}</div>
                  <div class="text-sm text-gray-500">{$_('join.standard_access')}</div>
                {/if}
              </div>
            </div>
          </div>
          {/if}

          <!-- Invite code input -->
          <div class="mb-6 p-3 md:p-4 border border-gray-200 rounded-xl">
            <label for="invite-code" class="block text-sm font-medium text-gray-700 mb-2">
              {#if inviteValid}
                {$_('join.invitation_code')}
              {:else if $realmOpenRegistration || $testMode}
                {$_('join.have_invite')}
              {:else}
                {$_('join.invitation_code')} <span class="text-red-500">*</span>
              {/if}
            </label>
            <div class="flex gap-2">
              <input
                id="invite-code"
                type="text"
                bind:value={inviteCode}
                on:keydown={(e) => { if (e.key === 'Enter' && inviteCode && !inviteChecking) validateInvite(); }}
                placeholder={$_('join.paste_invite')}
                disabled={inviteValid}
                class={cn(
                  "min-w-0 flex-1 px-3 py-2 border rounded-lg text-sm focus:ring-gray-900 focus:border-gray-900",
                  inviteValid ? "border-green-300 bg-green-50 text-green-800" : "border-gray-300"
                )}
              />
              {#if inviteValid}
                <button
                  on:click={() => { inviteCode = ''; inviteValid = false; inviteProfile = ''; inviteError = ''; }}
                  class="shrink-0 px-3 md:px-4 py-2 text-sm font-medium border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
                >
                  {$_('join.clear')}
                </button>
              {:else}
                <button
                  on:click={validateInvite}
                  disabled={!inviteCode || inviteChecking}
                  class="shrink-0 px-3 md:px-4 py-2 bg-gray-900 text-white text-sm font-medium rounded-lg hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {#if inviteChecking}
                    <Spinner size="4" color="white" />
                  {:else}
                    {$_('join.validate')}
                  {/if}
                </button>
              {/if}
            </div>
            {#if inviteProfile}
              <p class="mt-2 text-sm text-green-600 flex items-center gap-1">
                <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                  <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" />
                </svg>
                {$_('join.valid_grants', { values: { profile: inviteProfile } })}
              </p>
            {/if}
            {#if inviteError && !inviteChecking && !inviteValid}
              <p class="mt-2 text-sm text-red-600">{inviteError}</p>
            {/if}
            {#if $testModeUserSelfRegistration && !inviteValid}
              <p class="mt-2 text-xs text-gray-400 flex items-start gap-1.5">
                <svg class="w-3.5 h-3.5 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span>
                  {$_('join.test_mode_codes')}
                </span>
              </p>
            {/if}
          </div>
          
          <div class="flex gap-3">
            {#if prevStepId}
              <button
                on:click={goBack}
                class="flex-1 py-3 md:py-4 px-4 md:px-6 border border-gray-300 text-gray-700 font-medium rounded-xl hover:bg-gray-50 transition-all"
              >
                {$_('setup.wizard.back')}
              </button>
            {/if}
            <button
              on:click={handleJoin}
              disabled={loading || inviteRequired}
              class="flex-1 py-3 md:py-4 px-4 md:px-6 bg-gray-900 hover:bg-gray-800 text-white font-medium rounded-xl transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {#if loading}
                <Spinner size="5" color="white" />
                <span>{$_('join.joining')}</span>
              {:else}
                <span>{$_('join.join_realm')}</span>
              {/if}
            </button>
          </div>
        </div>

      <!-- Step: Success -->
      {:else if currentStep === 'success'}
        <div class="bg-white rounded-2xl shadow-xl p-5 md:p-8 border border-gray-100 text-center">
          <div class="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-6">
            <svg class="w-10 h-10 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
            </svg>
          </div>
          
          <h2 class="text-2xl font-bold text-gray-900 mb-2">{$_('join.welcome_success', { values: { name: realmName } })}</h2>
          <p class="text-gray-500 mb-8">
            {$_('join.join_success_body')}
          </p>
          
          <a
            href={resolve('/extensions/member_dashboard')}
            class="inline-flex items-center justify-center w-full py-4 px-6 bg-gray-900 hover:bg-gray-800 text-white font-medium rounded-xl transition-all gap-2"
          >
            <span>{$_('join.go_member_dashboard')}</span>
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"></path>
            </svg>
          </a>
        </div>
      {/if}
    </div>
  </div>
</div>
