#!/usr/bin/env python3
"""
Add per-extension `sidebar_label` (multilingual) to every extension's manifest.json.

Background — Issue #168 (Layered Realm)
---------------------------------------
The realm sidebar used to derive its display labels from `extensions.<id>.sidebar`
keys baked into realm_frontend's locale JSON files. With the Layered Realm work,
extensions are loaded at runtime from file_registry, and realm_frontend can no
longer assume that the labels are bundled. Each extension must therefore ship
its own multilingual sidebar label inside its manifest.

Contract
--------
After running this script, every extension manifest.json will include a
`sidebar_label` object of the form:

    "sidebar_label": {
        "en": "Voting",
        "es": "Votación",
        "de": "Abstimmung",
        "fr": "Vote",
        "it": "Voto",
        "zh-CN": "投票",
        "ca-valencia": "Votació"
    }

The realm_backend `get_sidebar_manifests()` query echoes this object straight
through, and realm_frontend's Sidebar.svelte picks the entry for the active
locale (with graceful fallbacks: <lang> → "en" → first key → manifest.name →
extension id). See `src/realm_frontend/src/routes/(sidebar)/Sidebar.svelte`.

Idempotency
-----------
- Manifests without `sidebar_label` get the full catalog from `LABELS`.
- Manifests that already have `sidebar_label` are merged: missing locale keys
  are filled from `LABELS` without removing extra keys or overwriting existing
  translations. Use `--force` to replace the whole object.
- Manifests for extension ids not in `LABELS` are left untouched and a
  warning is printed.
- Output JSON preserves the insertion order of existing keys; the new
  `sidebar_label` is appended just after `description` (or at the end if
  `description` is missing) so the diff is local and easy to review.

Usage
-----
    python3 scripts/add_sidebar_labels.py
    python3 scripts/add_sidebar_labels.py --force
    python3 scripts/add_sidebar_labels.py --extensions-dir ../realms-extensions/extensions
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# --- Translation table ------------------------------------------------------
# Keep this in sync with the locales actually shipped by realm_frontend
# (src/realm_frontend/src/lib/i18n/locales/*.json).  Adding a new locale here
# is enough — realm_frontend will pick it up automatically because the lookup
# is dynamic on the active $locale.
LOCALES = ("en", "es", "de", "fr", "it", "zh-CN", "ca-valencia")

LABELS: Dict[str, Dict[str, str]] = {
    "access_manager": {
        "en": "Department Management",
        "es": "Gestión de Departamentos",
        "de": "Abteilungsverwaltung",
        "fr": "Gestion des Départements",
        "it": "Gestione Dipartimenti",
        "zh-CN": "部门管理",
        "ca-valencia": "Gestió de Departaments",
    },
    "admin_dashboard": {
        "en": "Data Explorer",
        "es": "Explorador de Datos",
        "de": "Daten-Explorer",
        "fr": "Explorateur de Données",
        "it": "Esploratore Dati",
        "zh-CN": "数据浏览器",
        "ca-valencia": "Explorador de Dades",
    },
    "budget_manager": {
        "en": "Budget",
        "es": "Presupuesto",
        "de": "Haushalt",
        "fr": "Budget",
        "it": "Bilancio",
        "zh-CN": "预算",
        "ca-valencia": "Pressupost",
    },
    "census": {
        "en": "Census",
        "es": "Censo",
        "de": "Volkszählung",
        "fr": "Recensement",
        "it": "Censimento",
        "zh-CN": "人口普查",
        "ca-valencia": "Cens",
    },
    "codex_viewer": {
        "en": "Codices",
        "es": "Códices",
        "de": "Codices",
        "fr": "Codices",
        "it": "Codici",
        "zh-CN": "法典",
        "ca-valencia": "Còdex",
    },
    "demo_simulator": {
        "en": "Demo Simulator",
        "es": "Simulador de Demo",
        "de": "Demo-Simulator",
        "fr": "Simulateur de Démo",
        "it": "Simulatore Demo",
        "zh-CN": "演示模拟器",
        "ca-valencia": "Simulador de Demo",
    },
    "department_docs": {
        "en": "Department Docs",
        "es": "Documentos del Departamento",
        "de": "Abteilungsdokumente",
        "fr": "Documents du Département",
        "it": "Documenti di Dipartimento",
        "zh-CN": "部门文档",
        "ca-valencia": "Documents del Departament",
    },
    "erd_explorer": {
        "en": "ERD Explorer",
        "es": "Explorador ERD",
        "de": "ERD-Explorer",
        "fr": "Explorateur ERD",
        "it": "Esploratore ERD",
        "zh-CN": "ERD 浏览器",
        "ca-valencia": "Explorador ERD",
    },
    "extensions_manager": {
        "en": "Menus",
        "es": "Menús",
        "de": "Menüs",
        "fr": "Menus",
        "it": "Menu",
        "zh-CN": "菜单",
        "ca-valencia": "Menús",
    },
    "hello_sandboxed": {
        "en": "Hello (Sandboxed)",
        "es": "Hola (Aislado)",
        "de": "Hallo (Sandboxed)",
        "fr": "Bonjour (Sandboxé)",
        "it": "Ciao (Sandboxed)",
        "zh-CN": "你好（沙盒）",
        "ca-valencia": "Hola (Aïllat)",
    },
    "hello_world": {
        "en": "Hello World",
        "es": "Hola Mundo",
        "de": "Hallo Welt",
        "fr": "Bonjour le Monde",
        "it": "Ciao Mondo",
        "zh-CN": "你好世界",
        "ca-valencia": "Hola Món",
    },
    "import_export": {
        "en": "Import & Export",
        "es": "Importar y Exportar",
        "de": "Import & Export",
        "fr": "Import et Export",
        "it": "Importa ed Esporta",
        "zh-CN": "导入导出",
        "ca-valencia": "Importació i Exportació",
    },
    "justice_litigation": {
        "en": "Justice",
        "es": "Justicia",
        "de": "Justiz",
        "fr": "Justice",
        "it": "Giustizia",
        "zh-CN": "司法",
        "ca-valencia": "Justícia",
    },
    "land_registry": {
        "en": "Land Registry",
        "es": "Registro de Tierras",
        "de": "Grundbuch",
        "fr": "Cadastre",
        "it": "Catasto",
        "zh-CN": "土地登记",
        "ca-valencia": "Registre de Terres",
    },
    "llm_chat": {
        "en": "AI Assistant",
        "es": "Asistente IA",
        "de": "KI-Assistent",
        "fr": "Assistant IA",
        "it": "Assistente AI",
        "zh-CN": "AI 助手",
        "ca-valencia": "Assistent IA",
    },
    "managed_services": {
        "en": "Managed Services",
        "es": "Servicios Gestionados",
        "de": "Verwaltete Dienste",
        "fr": "Services Gérés",
        "it": "Servizi Gestiti",
        "zh-CN": "托管服务",
        "ca-valencia": "Serveis Gestionats",
    },
    "market_place": {
        "en": "Marketplace",
        "es": "Mercado",
        "de": "Marktplatz",
        "fr": "Marché",
        "it": "Mercato",
        "zh-CN": "市场",
        "ca-valencia": "Mercat",
    },
    "member_dashboard": {
        "en": "My Dashboard",
        "es": "Mi Panel",
        "de": "Mein Dashboard",
        "fr": "Mon Tableau de Bord",
        "it": "Mio Pannello",
        "zh-CN": "我的控制台",
        "ca-valencia": "El Meu Panell",
    },
    "member_manager": {
        "en": "Members",
        "es": "Miembros",
        "de": "Mitglieder",
        "fr": "Membres",
        "it": "Membri",
        "zh-CN": "成员",
        "ca-valencia": "Membres",
    },
    "metrics": {
        "en": "Financial Reports",
        "es": "Informes Financieros",
        "de": "Finanzberichte",
        "fr": "Rapports Financiers",
        "it": "Report Finanziari",
        "zh-CN": "财务报告",
        "ca-valencia": "Informes Financers",
    },
    "migration_console": {
        "en": "Migration Console",
        "es": "Consola de Migración",
        "de": "Migrationskonsole",
        "fr": "Console de Migration",
        "it": "Console di Migrazione",
        "zh-CN": "迁移控制台",
        "ca-valencia": "Consola de Migració",
    },
    "mundus_explorer": {
        "en": "Realms Network",
        "es": "Red de Realms",
        "de": "Realms-Netzwerk",
        "fr": "Réseau Realms",
        "it": "Rete Realms",
        "zh-CN": "Realms 网络",
        "ca-valencia": "Xarxa de Realms",
    },
    "notifications": {
        "en": "Notifications",
        "es": "Notificaciones",
        "de": "Benachrichtigungen",
        "fr": "Notifications",
        "it": "Notifiche",
        "zh-CN": "通知",
        "ca-valencia": "Notificacions",
    },
    "package_manager": {
        "en": "Package Manager",
        "es": "Gestor de Paquetes",
        "de": "Paketmanager",
        "fr": "Gestionnaire de Paquets",
        "it": "Gestore Pacchetti",
        "zh-CN": "包管理器",
        "ca-valencia": "Gestor de Paquets",
    },
    "passport_verification": {
        "en": "Passport Verification",
        "es": "Verificación de Pasaporte",
        "de": "Pass-Verifizierung",
        "fr": "Vérification du Passeport",
        "it": "Verifica Passaporto",
        "zh-CN": "护照验证",
        "ca-valencia": "Verificació de Passaport",
    },
    "procurement": {
        "en": "Procurement",
        "es": "Adquisiciones",
        "de": "Beschaffung",
        "fr": "Achats",
        "it": "Approvvigionamento",
        "zh-CN": "采购",
        "ca-valencia": "Compres",
    },
    "public_dashboard": {
        "en": "My Realm",
        "es": "Mi Reino",
        "de": "Mein Reich",
        "fr": "Mon Royaume",
        "it": "Il Mio Regno",
        "zh-CN": "我的领域",
        "ca-valencia": "El Meu Regne",
    },
    "realm_settings": {
        "en": "Realm Settings",
        "es": "Configuración del Reino",
        "de": "Reichseinstellungen",
        "fr": "Paramètres du Royaume",
        "it": "Impostazioni del Regno",
        "zh-CN": "领域设置",
        "ca-valencia": "Configuració del Regne",
    },
    "role_manager": {
        "en": "User Management",
        "es": "Gestión de Usuarios",
        "de": "Benutzerverwaltung",
        "fr": "Gestion des Utilisateurs",
        "it": "Gestione Utenti",
        "zh-CN": "用户管理",
        "ca-valencia": "Gestió d'Usuaris",
    },
    "system_info": {
        "en": "System Info",
        "es": "Información del Sistema",
        "de": "Systeminformationen",
        "fr": "Infos Système",
        "it": "Info Sistema",
        "zh-CN": "系统信息",
        "ca-valencia": "Informació del Sistema",
    },
    "task_monitor": {
        "en": "Task Monitor",
        "es": "Monitor de Tareas",
        "de": "Aufgaben-Monitor",
        "fr": "Moniteur de Tâches",
        "it": "Monitor Attività",
        "zh-CN": "任务监视器",
        "ca-valencia": "Monitor de Tasques",
    },
    "test_bench": {
        "en": "Test Bench",
        "es": "Banco de Pruebas",
        "de": "Testumgebung",
        "fr": "Banc de Test",
        "it": "Banco di Prova",
        "zh-CN": "测试台",
        "ca-valencia": "Banc de Proves",
    },
    "vault": {
        "en": "Vault",
        "es": "Bóveda",
        "de": "Tresor",
        "fr": "Coffre",
        "it": "Caveau",
        "zh-CN": "金库",
        "ca-valencia": "Cofre",
    },
    "voting": {
        "en": "Voting",
        "es": "Votación",
        "de": "Abstimmung",
        "fr": "Vote",
        "it": "Voto",
        "zh-CN": "投票",
        "ca-valencia": "Votació",
    },
    "welcome": {
        "en": "Welcome",
        "es": "Bienvenida",
        "de": "Willkommen",
        "fr": "Bienvenue",
        "it": "Benvenuto",
        "zh-CN": "欢迎",
        "ca-valencia": "Benvingut",
    },
    "zone_selector": {
        "en": "Zones",
        "es": "Zonas",
        "de": "Zonen",
        "fr": "Zones",
        "it": "Zone",
        "zh-CN": "区域",
        "ca-valencia": "Zones",
    },
}


def _default_extensions_dir() -> Path:
    here = Path(__file__).resolve().parent
    in_repo = here.parents[1] / "extensions" / "extensions"
    if in_repo.is_dir():
        return in_repo.resolve()
    return (here.parent.parent / "realms-extensions" / "extensions").resolve()


def _load_json_preserving_order(path: Path) -> "OrderedDict[str, object]":
    # `object_pairs_hook=OrderedDict` keeps insertion order so the diff stays
    # local — important for code review of these manifests.
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh, object_pairs_hook=OrderedDict)


def _dump_json_preserving_order(path: Path, data: "OrderedDict[str, object]") -> None:
    # Match the project's existing manifest indentation (2 spaces, trailing nl).
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _catalog_label(label: Dict[str, str]) -> "OrderedDict[str, str]":
    return OrderedDict((locale, label[locale]) for locale in LOCALES)


def _merge_sidebar_label(
    existing: object,
    catalog: Dict[str, str],
) -> Tuple["OrderedDict[str, str]", List[str]]:
    """Fill missing catalog locales without overwriting or removing keys."""
    base: "OrderedDict[str, str]" = OrderedDict()
    if isinstance(existing, dict):
        for key, value in existing.items():
            if isinstance(key, str) and isinstance(value, str):
                base[key] = value

    added: List[str] = []
    for locale in LOCALES:
        if locale not in base and locale in catalog:
            base[locale] = catalog[locale]
            added.append(locale)
    return base, added


def _insert_sidebar_label(
    manifest: "OrderedDict[str, object]",
    label: "OrderedDict[str, str]",
) -> "OrderedDict[str, object]":
    """Return a NEW OrderedDict with `sidebar_label` placed right after
    `description` (so manifests stay readable). Falls back to appending."""
    if "description" not in manifest:
        new = OrderedDict(manifest)
        new["sidebar_label"] = label
        return new

    out: "OrderedDict[str, object]" = OrderedDict()
    inserted = False
    for k, v in manifest.items():
        out[k] = v
        if not inserted and k == "description":
            out["sidebar_label"] = label
            inserted = True
    return out


def _update_sidebar_label(
    manifest: "OrderedDict[str, object]",
    label: "OrderedDict[str, str]",
) -> "OrderedDict[str, object]":
    if "sidebar_label" not in manifest:
        return _insert_sidebar_label(manifest, label)
    out: "OrderedDict[str, object]" = OrderedDict()
    for k, v in manifest.items():
        out[k] = label if k == "sidebar_label" else v
    return out


def _process(
    manifest_path: Path,
    *,
    force: bool,
    dry_run: bool,
) -> str:
    manifest = _load_json_preserving_order(manifest_path)
    ext_id = (manifest.get("name") or manifest_path.parent.name) if isinstance(manifest, dict) else manifest_path.parent.name

    catalog = LABELS.get(str(ext_id))
    if not catalog:
        return f"skip   {ext_id:<22} (no label table entry — extend LABELS in this script)"

    existing = manifest.get("sidebar_label")
    if force:
        new_label = _catalog_label(catalog)
        action_keys = list(new_label.keys())
        detail = "force overwrite"
    elif existing is None:
        new_label = _catalog_label(catalog)
        action_keys = list(new_label.keys())
        detail = "insert"
    else:
        new_label, action_keys = _merge_sidebar_label(existing, catalog)
        if not action_keys:
            return f"keep   {ext_id:<22} (sidebar_label already complete)"
        detail = f"merge +{','.join(action_keys)}"

    new_manifest = (
        _insert_sidebar_label(manifest, new_label)
        if existing is None
        else _update_sidebar_label(manifest, new_label)
    )

    if dry_run:
        return f"would  {ext_id:<22} {detail}"

    _dump_json_preserving_order(manifest_path, new_manifest)
    return f"write  {ext_id:<22} {detail}"


def _iter_extension_manifests(extensions_dir: Path) -> Iterable[Path]:
    if not extensions_dir.is_dir():
        raise SystemExit(f"Extensions dir not found: {extensions_dir}")
    for entry in sorted(extensions_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        manifest = entry / "manifest.json"
        if manifest.exists():
            yield manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default_dir = _default_extensions_dir()
    p.add_argument("--extensions-dir", type=Path, default=default_dir,
                   help=f"Path to the extensions repo dir (default: {default_dir})")
    p.add_argument("--force", action="store_true",
                   help="Replace an existing sidebar_label block with the catalog table")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would change without writing files")
    p.add_argument("--only", nargs="*", default=None,
                   help="Limit to specific extension ids (default: all)")
    args = p.parse_args(argv)

    targets = list(_iter_extension_manifests(args.extensions_dir))
    if args.only:
        wanted = set(args.only)
        targets = [m for m in targets if m.parent.name in wanted]
        missing = wanted - {m.parent.name for m in targets}
        for name in sorted(missing):
            print(f"skip   {name:<22} (not found under {args.extensions_dir})")

    print(f"# {len(targets)} manifest(s) under {args.extensions_dir}")
    for manifest_path in targets:
        try:
            line = _process(manifest_path, force=args.force, dry_run=args.dry_run)
        except Exception as e:  # noqa: BLE001
            line = f"error  {manifest_path.parent.name:<22} ({e})"
        print(line)

    print("# done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
