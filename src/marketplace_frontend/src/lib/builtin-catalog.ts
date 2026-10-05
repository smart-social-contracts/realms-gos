// Static catalog of built-in extensions and codices shipped with Realms.
//
// When the marketplace_backend canister is empty or unreachable, the UI
// may fall back to this list for offline/dev — never as marketplace-verified.

import type { ExtensionListing, CodexListing } from './marketplace-client';
import {
  BUILTIN_VERIFICATION_NOTES,
  BUILTIN_VERIFICATION_STATUS,
} from './verified-filter';

const now = Date.now() * 1_000_000;

function ext(
  id: string,
  name: string,
  description: string,
  version: string,
  icon: string,
  cats: string,
  shots = '',
): ExtensionListing {
  return {
    extension_id: id,
    developer: 'Realms GOS team',
    developer_name: 'Realms GOS team',
    name,
    description,
    version,
    price_e8s: 0,
    icon,
    categories: cats,
    screenshots: shots,
    file_registry_canister_id: '',
    file_registry_namespace: `ext/${id}/${version}`,
    download_url: `https://github.com/smart-social-contracts/realms-gos/tree/main/extensions/extensions/${id}`,
    installs: 0,
    likes: 0,
    verification_status: BUILTIN_VERIFICATION_STATUS,
    verification_notes: BUILTIN_VERIFICATION_NOTES,
    is_active: true,
    created_at: now,
    updated_at: now,
  };
}

function codex(
  id: string,
  alias: string,
  name: string,
  description: string,
  version: string,
  icon: string,
  cats: string,
  realmType: string,
): CodexListing {
  return {
    codex_id: id,
    codex_alias: alias,
    realm_type: realmType,
    developer: 'Realms GOS team',
    developer_name: 'Realms GOS team',
    name,
    description,
    version,
    price_e8s: 0,
    icon,
    categories: cats,
    file_registry_canister_id: '',
    file_registry_namespace: `codex/${id}/${version}`,
    installs: 0,
    likes: 0,
    verification_status: BUILTIN_VERIFICATION_STATUS,
    verification_notes: BUILTIN_VERIFICATION_NOTES,
    is_active: true,
    created_at: now,
    updated_at: now,
  };
}

export const builtinExtensions: ExtensionListing[] = [
  // Public Services
  ext(
    'member_dashboard',
    'My Dashboard',
    'Personal dashboard for members to manage their government services and documents',
    '1.1.3', '📊', 'public_services',
    'screenshots/01-overview.png',
  ),
  ext(
    'justice_litigation',
    'Justice',
    'Comprehensive legal case management with courts, judges, verdicts, penalties, and appeals',
    '0.2.0', '⚖️', 'public_services',
  ),
  ext(
    'land_registry',
    'Land Registry',
    'Property registration and land ownership management',
    '1.1.0', '📍', 'public_services',
  ),
  ext(
    'passport_verification',
    'Passport Verification',
    'Rarimo ZK Passport verification extension for secure identity verification using zero-knowledge proofs',
    '1.0.4', '🪪', 'public_services',
  ),
  // Governance
  ext(
    'voting',
    'Voting',
    'Governance voting system for submitting and voting on proposals to execute Python code',
    '1.0.3', '🗳️', 'governance',
  ),
  ext(
    'codex_viewer',
    'Codices',
    'View and browse Codex automation scripts',
    '1.0.0', '📜', 'governance',
  ),
  // Administration
  ext(
    'admin_dashboard',
    'Data Explorer',
    'Browse, export, and import realm entities',
    '1.1.0', '⚙️', 'administration',
  ),
  ext(
    'realm_settings',
    'Realm Settings',
    'Configure realm name, manifesto, branding, registration, and infrastructure settings',
    '1.0.0', '⚙️', 'administration',
  ),
  // Territory
  ext(
    'zone_selector',
    'Zones',
    'Allow users to set their zones of influence via geolocation or map selection',
    '1.0.0', '🗺️', 'land_territory',
  ),
  // Finances
  ext(
    'vault',
    'Vault',
    'Admin treasury dashboard for Realms. View token balances, transaction history, and perform ad-hoc transfers.',
    '0.2.0', '💰', 'finances',
  ),
  ext(
    'metrics',
    'Financials',
    'Financial statements, accounting data, and analytics dashboard for realm performance',
    '1.1.0', '📈', 'finances',
  ),
  // Settings
  ext(
    'package_manager',
    'Package Manager',
    'Install, update and uninstall extensions and codex packages from connected file registries, and browse the marketplace',
    '0.3.0', '📦', 'settings',
  ),
  ext(
    'managed_services',
    'Managed Services',
    'Version management, self-upgrade, and credit purchasing',
    '0.1.1', '☁️', 'settings',
  ),
  // Other
  ext(
    'public_dashboard',
    'Public Dashboard',
    'Public dashboard with analytics and statistics for the realm',
    '1.0.3', '📊', 'other',
  ),
  ext(
    'llm_chat',
    'AI Governance Assistant',
    'AI-powered assistant for governance and legal questions',
    '0.1.3', '🧠', 'other',
  ),
  ext(
    'demo_simulator',
    'Demo Simulator',
    'Continuous demo data simulator for testing',
    '1.1.2', '🧪', 'other',
  ),
  ext(
    'erd_explorer',
    'ERD Explorer',
    'Interactive Entity Relationship Diagram explorer for visualizing and navigating Realms data structure',
    '1.0.3', '🔗', 'other',
  ),
  ext(
    'hello_world',
    'Hello World',
    'Simple hello world extension — runtime extension demo',
    '0.1.1', '✨', 'other',
  ),
  ext(
    'system_info',
    'System Info',
    'System information dashboard for realm administrators',
    '1.0.3', '📈', 'other',
    'screenshots/01-overview.png',
  ),
  ext(
    'task_monitor',
    'Task Monitor',
    'Task monitoring and management dashboard for administrators',
    '1.0.0', '📋', 'other',
  ),
  ext(
    'welcome',
    'Welcome',
    'Welcome page and onboarding for new users',
    '1.0.3', '🏠', 'other',
  ),
];

export const builtinCodices: CodexListing[] = [
  codex(
    'agora', 'agora',
    'Agora',
    'A direct democracy funded by monthly membership dues. Citizens verify identity via ZK passport, pay monthly bills, submit and vote on proposals, with real-time double-entry accounting.',
    '1.0.0', '🏛️', 'governance',
    'agora',
  ),
  codex(
    'dominion', 'dominion',
    'Dominion',
    'A direct democracy with progressive taxation. Citizens propose and vote on decisions collectively while a tax system funds realm operations.',
    '0.1.0', '👑', 'governance',
    'dominion',
  ),
  codex(
    'syntropia', 'syntropia',
    'Syntropia',
    'A full-featured representative democracy with three branches of government — parliament, executive, and judiciary — plus universal welfare, progressive taxation, and territorial governance.',
    '1.0.0', '🌿', 'governance',
    'syntropia',
  ),
];
