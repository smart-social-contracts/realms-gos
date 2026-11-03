import { browser } from "$app/environment";
import { get } from "svelte/store";
import { register, isLoading, addMessages, locale } from "svelte-i18n";
import { LOCALE_CATALOG, resolveUiLocale } from "./realmLocales";
import { bootI18n } from "./boot";

export { LOCALE_CATALOG };
export { bootI18n } from "./boot";
export const supportedLocales = [...LOCALE_CATALOG];

// English is seeded synchronously in bootI18n() — do not register() it.
// A register() loader keeps locale=null until the dynamic import resolves,
// which is what made $_() throw and left the loading globe up on reload.
register("es", () => import("./locales/es.json"));
register("de", () => import("./locales/de.json"));
register("fr", () => import("./locales/fr.json"));
register("zh-CN", () => import("./locales/zh-CN.json"));
register("it", () => import("./locales/it.json"));
register("ca", () => import("./locales/ca.json"));
register("eu", () => import("./locales/eu.json"));
register("gl", () => import("./locales/gl.json"));
register("rm", () => import("./locales/rm.json"));
register("zh-HK", () => import("./locales/zh-HK.json"));
register("ca-valencia", () => import("./locales/ca-valencia.json"));

// Helper function to wait for locale to be ready
export function waitLocale(): Promise<void> {
  return new Promise<void>((resolve) => {
    if (get(locale) && !get(isLoading)) {
      resolve();
      return;
    }
    const unsubscribe = isLoading.subscribe(($isLoading) => {
      if (!$isLoading) {
        unsubscribe();
        resolve();
      }
    });
  });
}

export function applyResolvedLocale(input: {
  userLocale?: string | null;
  languages?: readonly string[] | null;
  primaryLanguage?: string | null;
}): string {
  const next = resolveUiLocale(input.userLocale, input.languages, input.primaryLanguage);
  locale.set(next);
  if (browser && typeof document !== "undefined") {
    document.documentElement.lang = next;
  }
  return next;
}

function getPreferredLocale(): string {
  // Host chrome has no language switcher. Until realm + user settings load,
  // start from English; applyResolvedLocale then uses user → primary → en.
  return "en";
}

// ---------------------------------------------------------------------------
// Runtime extension translations — lazy, same-origin loading
// ---------------------------------------------------------------------------
// Each extension's i18n files live at:
//   /ext/{ext_id}/{version}/frontend/i18n/{locale}.json
// on the realm's own frontend asset canister (copied there during install).
// Translations are loaded lazily — one extension, one locale, on demand —
// when the extension page is mounted, not at app startup.
// ---------------------------------------------------------------------------

const _loadedExtLocales = new Set<string>();

/** Strip legacy `{ "extensions": { "<id>": { ... } } }` wrapper when present. */
function normalizeExtensionTranslationObject(
  extensionId: string,
  raw: unknown,
): Record<string, unknown> | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const o = raw as Record<string, unknown>;
  const nested = o.extensions;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    const inner = (nested as Record<string, unknown>)[extensionId];
    if (inner && typeof inner === "object" && !Array.isArray(inner)) {
      return inner as Record<string, unknown>;
    }
  }
  return o as Record<string, unknown>;
}

/**
 * Load translations for a single extension and locale from the realm's own
 * frontend asset canister (same-origin). Safe to call multiple times — skips
 * if the same extension+locale was already loaded.
 */
export async function loadExtensionTranslation(
  extId: string,
  version: string,
  currentLocale: string,
): Promise<void> {
  const localesToLoad = [currentLocale];
  if (currentLocale !== "en") localesToLoad.push("en");

  for (const loc of localesToLoad) {
    const key = `${extId}@${version}:${loc}`;
    if (_loadedExtLocales.has(key)) continue;
    _loadedExtLocales.add(key);

    const url = `/ext/${extId}/${version}/frontend/i18n/${loc}.json`;
    try {
      const res = await fetch(url);
      if (!res.ok) continue;
      const raw = await res.json();
      const translations = normalizeExtensionTranslationObject(extId, raw);
      if (translations) {
        addMessages(loc, { extensions: { [extId]: translations } });
      }
    } catch {
      // Extension doesn't ship i18n for this locale — that's fine.
    }
  }
}

// Find and register all extension translations
export async function loadExtensionTranslations() {
  // Define type for module imports
  type JsonModule = { default: Record<string, any> };
  
  try {
    // Define supported locales for extension translations
    const supportedExtensionLocales = [
      { code: 'en', modules: import.meta.glob('./locales/extensions/*/en.json') },
      { code: 'es', modules: import.meta.glob('./locales/extensions/*/es.json') },
      { code: 'de', modules: import.meta.glob('./locales/extensions/*/de.json') },
      { code: 'fr', modules: import.meta.glob('./locales/extensions/*/fr.json') },
      { code: 'it', modules: import.meta.glob('./locales/extensions/*/it.json') },
      { code: 'zh-CN', modules: import.meta.glob('./locales/extensions/*/zh-CN.json') },
      { code: 'ca-valencia', modules: import.meta.glob('./locales/extensions/*/ca-valencia.json') }
    ];

    // Process all locales
    await Promise.all(supportedExtensionLocales.map(async ({ code, modules }) => {
      for (const path of Object.keys(modules)) {
        try {
          // Extract extension ID from path
          const extensionId = path.split('/').slice(-2)[0];
          const module = await modules[path]();
          const translations = (module as JsonModule).default;
          
          addMessages(code, {
            extensions: {
              [extensionId]: translations
            }
          });
          
          // console.log(`Loaded translations for extension: ${extensionId}, locale: ${code}, translations: ${JSON.stringify(translations)}`);
        } catch (err) {
          console.error(`Failed to load extension translation: ${path} for locale: ${code}`, err);
        }
      }
    }));
    
    console.log('All extension translations loaded successfully');
  } catch (error) {
    console.error('Error loading extension translations:', error);
  }
}

// Seed English + set locale before any component can call $_().
bootI18n(browser ? getPreferredLocale() : "en");

if (browser) {
  void waitLocale().then(async () => {
    console.log("i18n initialized in realm_frontend");
    // Build-time bundled extension translations (legacy path; will be empty
    // in fully layered deployments because no extension code is shipped in
    // the realm_frontend WASM). Runtime extension translations are loaded
    // lazily per-extension via loadExtensionTranslation().
    await loadExtensionTranslations();
    console.log("All translations (core + extensions) loaded");
  });
}
