// Small UI formatting helpers.

export function formatPrice(priceE8s: number): string {
  if (!priceE8s || priceE8s === 0) return 'Free';
  const icp = priceE8s / 100_000_000;
  return `${icp.toFixed(icp < 1 ? 4 : 2)} ICP`;
}

export function formatPriceUsd(usdCents: number): string {
  if (!usdCents) return '$0';
  const dollars = usdCents / 100;
  return Number.isInteger(dollars) ? `$${dollars}` : `$${dollars.toFixed(2)}`;
}

export function formatCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

export function formatTimeAgo(tsNs: number): string {
  if (!tsNs) return '';
  const seconds = Math.max(0, (Date.now() - tsNs / 1_000_000) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 86400 * 30) return `${Math.floor(seconds / 86400)}d ago`;
  if (seconds < 86400 * 365) return `${Math.floor(seconds / (86400 * 30))}mo ago`;
  return `${Math.floor(seconds / (86400 * 365))}y ago`;
}

export function formatDate(tsNs: number): string {
  if (!tsNs) return '';
  return new Date(tsNs / 1_000_000).toLocaleDateString();
}

export function categories(s: string): string[] {
  if (!s) return [];
  return s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);
}

export function screenshots(s: string): string[] {
  if (!s) return [];
  return s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);
}

export function shortPrincipal(p: string): string {
  if (!p || p.length < 16) return p;
  return `${p.slice(0, 5)}…${p.slice(-3)}`;
}

export function looksLikePrincipal(dev: string): boolean {
  return /^[a-z0-9]{5}-[a-z0-9]+/.test(dev);
}

export function developerLabel(dev: string, name = ''): string {
  if (name) return name;
  if (!dev) return '';
  return looksLikePrincipal(dev) ? shortPrincipal(dev) : dev;
}
