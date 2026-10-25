"""Realm language catalog and persistence (issue #361).

Canonical store: ``Realm.manifest_data`` keys ``languages`` (list of BCP-47
ids) and ``primary_language`` (one id that must be in that list). The same
shape is written by ``update_realm_config`` / ``apply_realm_config`` so the
Realm Settings extension can read and write it later.

UI locale resolve order: user override → realm primary → ``en``.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, List, Optional, Sequence, Tuple

# Keep in lockstep with src/realm_frontend/src/lib/i18n/realmLocales.ts
LOCALE_CATALOG: Tuple[Tuple[str, str], ...] = (
    ("en", "English"),
    ("es", "Español"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("it", "Italiano"),
    ("zh-CN", "中文 (简体)"),
    ("ca-valencia", "Valencià"),
)

CATALOG_IDS: Tuple[str, ...] = tuple(locale_id for locale_id, _label in LOCALE_CATALOG)
CATALOG_ID_SET = frozenset(CATALOG_IDS)

DEFAULT_LANGUAGE = "en"
FALLBACK_LOCALE = "en"

_MANIFEST_MAX = 4096


def locale_label(locale_id: str) -> str:
    for item_id, label in LOCALE_CATALOG:
        if item_id == locale_id:
            return label
    return locale_id


def _as_string_list(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
        return parts
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            if not isinstance(item, str):
                return None
            trimmed = item.strip()
            if trimmed:
                out.append(trimmed)
        return out
    return None


def normalize_languages(
    languages: Any,
    primary_language: Any,
    *,
    require_primary: bool = True,
) -> Tuple[Optional[List[str]], Optional[str], Optional[str]]:
    """Validate and normalize a language list + primary.

    Returns ``(languages, primary, error)``. On success ``error`` is None.
    """
    parsed = _as_string_list(languages)
    if parsed is None:
        return None, None, "languages must be a list of locale ids"
    if not parsed:
        return None, None, "languages must include at least one locale"

    seen = set()
    normalized: List[str] = []
    for locale_id in parsed:
        if locale_id not in CATALOG_ID_SET:
            return None, None, f"unsupported locale: {locale_id}"
        if locale_id in seen:
            continue
        seen.add(locale_id)
        normalized.append(locale_id)

    primary = primary_language
    if primary is None or primary == "":
        if require_primary:
            return None, None, "primary_language is required"
        primary = normalized[0]
    if not isinstance(primary, str):
        return None, None, "primary_language must be a string"
    primary = primary.strip()
    if primary not in normalized:
        return None, None, "primary_language must be one of the enabled languages"
    return normalized, primary, None


def load_manifest(realm) -> dict:
    raw = getattr(realm, "manifest_data", "") or "{}"
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_manifest(realm, manifest: dict) -> Optional[str]:
    serialized = json.dumps(manifest, separators=(",", ":"))
    if len(serialized) > _MANIFEST_MAX:
        return f"manifest_data would exceed {_MANIFEST_MAX} chars ({len(serialized)})"
    realm.manifest_data = serialized
    return None


def get_realm_languages(realm) -> Tuple[List[str], str]:
    """Return ``(languages, primary_language)`` with catalog-safe defaults."""
    manifest = load_manifest(realm)
    languages, primary, error = normalize_languages(
        manifest.get("languages"),
        manifest.get("primary_language"),
        require_primary=False,
    )
    if error or not languages or not primary:
        return [DEFAULT_LANGUAGE], DEFAULT_LANGUAGE
    return languages, primary


def apply_realm_languages(
    realm,
    languages: Any = None,
    primary_language: Any = None,
    *,
    replace_languages: bool = True,
) -> Tuple[Optional[List[str]], Optional[str], Optional[str]]:
    """Write languages + primary onto ``realm.manifest_data``.

    When ``replace_languages`` is False and ``languages`` is omitted, the
    existing list is kept and only primary is updated (still must be in list).
    """
    current_languages, current_primary = get_realm_languages(realm)
    next_languages = languages if languages is not None else current_languages
    if not replace_languages and languages is None:
        next_languages = current_languages
    next_primary = primary_language if primary_language is not None else current_primary

    normalized, primary, error = normalize_languages(
        next_languages, next_primary, require_primary=True
    )
    if error:
        return None, None, error

    manifest = load_manifest(realm)
    manifest["languages"] = normalized
    manifest["primary_language"] = primary
    save_error = save_manifest(realm, manifest)
    if save_error:
        return None, None, save_error
    return normalized, primary, None


def resolve_ui_locale(
    user_override: Any,
    languages: Optional[Sequence[str]] = None,
    primary_language: Optional[str] = None,
) -> str:
    """Resolve the UI locale: user override → realm primary → en."""
    enabled = [item for item in (languages or ()) if item in CATALOG_ID_SET]
    primary = (primary_language or "").strip()
    if primary not in enabled:
        primary = enabled[0] if enabled else FALLBACK_LOCALE

    override = (user_override or "").strip() if isinstance(user_override, str) else ""
    if override and override in enabled:
        return override
    if primary in CATALOG_ID_SET:
        return primary
    return FALLBACK_LOCALE


def user_locale_from_private_data(private_data: Any) -> str:
    if isinstance(private_data, str):
        try:
            private_data = json.loads(private_data or "{}")
        except (json.JSONDecodeError, TypeError):
            return ""
    if not isinstance(private_data, dict):
        return ""
    value = private_data.get("locale")
    if not isinstance(value, str):
        return ""
    return value.strip()


def validate_user_locale(locale: Any, languages: Iterable[str]) -> Optional[str]:
    """Empty locale means 'use realm primary'. Non-empty must be in the list."""
    if locale is None or locale == "":
        return None
    if not isinstance(locale, str):
        return "locale must be a string"
    trimmed = locale.strip()
    if not trimmed:
        return None
    allowed = {item for item in languages if item in CATALOG_ID_SET}
    if trimmed not in allowed:
        return "locale must be one of the realm languages"
    return None
