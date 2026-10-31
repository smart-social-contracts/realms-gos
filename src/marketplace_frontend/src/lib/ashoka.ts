/** Ashoka via the AI assistant service — general mode (no realm). Needs the user signed in. */

import { AI_URL, createAiSession, fetchAsUser } from "./aiSession.ts";

export { SignedOutError } from "./aiSession.ts";

export const ASK_URL = `${AI_URL}api/ask`;
export const SUGGESTIONS_URL = `${AI_URL}api/suggestions`;
export const PERSONA = "ashoka";
export const CHAT_TIMEOUT_MS = 360_000;

// Loaded on first use: auth.ts needs the browser's auth client, which ashoka.test.ts (plain node) cannot import.
const session = createAiSession(async () => (await import("./auth")).getIdentity());

export type StreamState = {
  text: string;
  thinking: string;
  status: string;
};

export function emptyStream(): StreamState {
  return { text: "", thinking: "", status: "" };
}

export function geisterNetwork(envName = "", hostname = ""): string {
  const env = envName.toLowerCase();
  if (env === "test" || env === "demo" || env === "staging") return env;
  if (hostname.includes("demo.realmsgos")) return "demo";
  if (hostname.includes("staging.realmsgos")) return "staging";
  return "test";
}

function runtimeNetwork(): string {
  let envName = "";
  try {
    envName = String((import.meta as { env?: { VITE_ENV_NAME?: string } }).env?.VITE_ENV_NAME || "");
  } catch {
    envName = "";
  }
  const hostname = typeof window !== "undefined" ? window.location.hostname : "";
  return geisterNetwork(envName, hostname);
}

export function applyStreamEvent(parsed: Record<string, unknown>, state: StreamState): void {
  const eventType = typeof parsed.type === "string" ? parsed.type : parsed.text ? "text" : "";
  const chunk = typeof parsed.text === "string" ? parsed.text : "";
  if (eventType === "status" && chunk) {
    state.status = chunk;
    return;
  }
  if (eventType === "thinking" && chunk) {
    state.thinking += chunk;
    return;
  }
  if (chunk) {
    state.text += chunk;
    state.status = "";
  }
}

export function applyStreamLine(line: string, state: StreamState): void {
  if (line.startsWith("data: ")) {
    const payload = line.slice(6);
    if (payload === "[DONE]") return;
    try {
      applyStreamEvent(JSON.parse(payload) as Record<string, unknown>, state);
    } catch {
      state.text += payload;
    }
    return;
  }
  if (line.trim() && !line.startsWith(":")) {
    state.text += line;
  }
}

export async function askAshoka(opts: {
  question: string;
  conversationId?: string | null;
  signal?: AbortSignal;
  onUpdate: (state: StreamState) => void;
}): Promise<string> {
  const body: Record<string, unknown> = {
    question: opts.question,
    stream: true,
    verbosity: 1,
    persona: PERSONA,
    network: runtimeNetwork(),
    page_context: {
      pathname: typeof window !== "undefined" ? window.location.pathname : "/about",
      title: "Realms GOS",
    },
  };
  if (opts.conversationId) body.conversation_id = opts.conversationId;

  const response = await fetchAsUser(session, ASK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal: opts.signal ?? AbortSignal.timeout(CHAT_TIMEOUT_MS),
  });

  if (!response.ok) {
    let detail = "";
    try {
      const errBody = await response.json();
      detail = typeof errBody?.error === "string" ? errBody.error : "";
    } catch {
      /* ignore */
    }
    throw new Error(detail || `Ashoka is unavailable (${response.status}).`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("Ashoka returned an empty stream.");

  const decoder = new TextDecoder();
  const state = emptyStream();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, { stream: true });
      for (const line of chunk.split("\n")) {
        applyStreamLine(line, state);
        opts.onUpdate({ ...state });
      }
    }
  } finally {
    reader.releaseLock();
  }
  return state.text.trim();
}

export function parseSuggestions(data: unknown): string[] {
  const raw =
    data && typeof data === "object" && Array.isArray((data as { suggestions?: unknown }).suggestions)
      ? (data as { suggestions: unknown[] }).suggestions
      : [];
  return raw
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .map((item) => item.trim())
    .slice(0, 5);
}

export async function fetchAshokaSuggestions(opts: { signal?: AbortSignal }): Promise<string[]> {
  const params = new URLSearchParams({
    persona: PERSONA,
    network: runtimeNetwork(),
  });
  try {
    const response = await fetchAsUser(session, `${SUGGESTIONS_URL}?${params}`, {
      headers: { Accept: "application/json" },
      signal: opts.signal,
    });
    return response.ok ? parseSuggestions(await response.json()) : [];
  } catch {
    return [];
  }
}
