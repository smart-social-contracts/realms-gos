#!/usr/bin/env python3
"""
Geister Agent Swarm Test - Run LLM-powered agents against staging realms.

Discovers realms dynamically from the registry and runs a diverse swarm of
agents (assistants, founders, and citizens) that join and perform governance
operations via the Geister API.

Any issues self-reported by agents (prefixed ISSUE:) are automatically filed
as GitHub Issues using `gh issue create`.

Usage:
    python test_agent_swarm.py
    AGENTS_COUNT=22 python test_agent_swarm.py
    GITHUB_REPOSITORY=org/repo GH_TOKEN=xxx python test_agent_swarm.py
"""
import json
import os
import sys
import re
import subprocess
import time
import requests as http_requests
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional, NamedTuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AGENTS_COUNT = int(os.getenv("AGENTS_COUNT", "22"))   # agents per realm
NETWORK = os.getenv("NETWORK", "staging")
GEISTER_API_URL = os.getenv("GEISTER_API_URL", "https://geister-api.realmsgos.dev")
RUN_DURATION_MINUTES = int(os.getenv("RUN_DURATION_MINUTES", "0"))       # 0 = no overall limit
FAILURE_THRESHOLD = float(os.getenv("FAILURE_THRESHOLD", "0.3"))        # 30% max failures
LOGS_DIR = Path(os.getenv("LOGS_DIR", "agent_logs"))
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")   # e.g. "smart-social-contracts/realms-gos"
GH_TOKEN = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN", "")
ISSUE_LABEL = os.getenv("ISSUE_LABEL", "agent-swarm")
RUN_ID = os.getenv("RUN_ID") or os.getenv("GITHUB_RUN_ID") or datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Persona roster definition
# AGENTS_COUNT agents are spread across realms using the ratio below.
# The list repeats as needed via modulo indexing.
# ---------------------------------------------------------------------------
class PersonaSlot(NamedTuple):
    persona: str           # name passed to geister --persona
    role_label: str        # human-readable label for logs
    emoji: str
    task: str              # LLM instruction for this agent (used as custom_telos fallback)
    template_name: str = ""  # if set, use this DB template instead of task


SLOT_ASHOKA = PersonaSlot(
    persona="ashoka",
    role_label="assistant",
    emoji="🏛️",
    task=(
        "Analyse this realm thoroughly. Use available tools to check realm status, "
        "active extensions, recent proposals, member count, and governance health. "
        "Summarise what you find. "
        "If anything looks broken, misconfigured, or suspicious, report it by writing "
        "'ISSUE: <one-line description>' on its own line for each problem found. "
        "Report what actions you took and what you observed."
    ),
)

SLOT_FOUNDER = PersonaSlot(
    persona="founder",
    role_label="founder",
    emoji="🏗️",
    task=(
        "You are a visionary Founder. Create a new realm by doing ONLY these two calls "
        "(keep it fast, do NOT call anything else):\n"
        "1. Call get_my_principal.\n"
        "2. Call registry_deploy_realm with these fields (dfx identity must have credits):\n"
        "   realm_name – a creative unique name (add a random 3-char suffix like 'Aurora_7kx')\n"
        "   description – one sentence explaining the realm's vision\n"
        "   welcome_message – a short greeting for new citizens\n"
        "   (logo and background are served from /images/logo.png and /images/background.png by convention)\n"
        "If deploy fails with 'insufficient credits', call registry_redeem_voucher "
        "with your principal and code 'BETA50', then retry the deploy.\n"
        "Report any failure as 'ISSUE: <description>'. Summarise what you created."
    ),
    template_name="Realm Founder",
)

# Citizen persona templates – agents are distributed equally across these
CITIZEN_PERSONAS: List[PersonaSlot] = [
    PersonaSlot(
        persona="compliant",
        role_label="citizen-compliant",
        emoji="✅",
        task=(
            "Join this realm as a member if you haven't already. "
            "Once joined, use db_schema to discover available entities. "
            "Check if there are any active proposals and cast a vote if possible. "
            "If anything fails or behaves unexpectedly, write 'ISSUE: <description>'. "
            "Report what actions you took."
        ),
        template_name="Citizen Onboarding",
    ),
    PersonaSlot(
        persona="watchful",
        role_label="citizen-watchful",
        emoji="👁️",
        task=(
            "Join this realm and carefully observe its governance. "
            "Check the codex rules, active mandates, and any recent transfers. "
            "Look for anything unusual: policy loopholes, stale proposals, "
            "excessive permissions, or discrepancies. "
            "Write 'ISSUE: <description>' for every concern you notice. "
            "Report your full observations."
        ),
        template_name="Citizen Onboarding",
    ),
    PersonaSlot(
        persona="exploiter",
        role_label="citizen-exploiter",
        emoji="😈",
        task=(
            "Join this realm and try to find edge cases or weaknesses. "
            "Attempt actions that might fail gracefully or expose bugs: "
            "double-joining, voting on a closed proposal, calling an extension with "
            "bad arguments, or querying non-existent entities. "
            "Write 'ISSUE: <description>' for any unexpected error, crash message, "
            "or missing validation you encounter. "
            "Report everything you tried and what happened."
        ),
        template_name="Citizen Onboarding",
    ),
]


# ---------------------------------------------------------------------------
# Agent registration (persistent profiles on the Geister API)
# ---------------------------------------------------------------------------

def _api_post(path: str, payload: Dict) -> Optional[Dict]:
    """POST JSON to the Geister API. Returns parsed response or None."""
    url = f"{GEISTER_API_URL}{path}"
    try:
        resp = http_requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except http_requests.exceptions.HTTPError as e:
        body = e.response.text[:200] if e.response is not None else ""
        code = e.response.status_code if e.response is not None else "?"
        print(f"  ⚠️  API POST {path} → HTTP {code}: {body}")
    except Exception as e:
        print(f"  ⚠️  API POST {path} failed: {e}")
    return None


def _api_put(path: str, payload: Dict) -> Optional[Dict]:
    """PUT JSON to the Geister API. Returns parsed response or None."""
    url = f"{GEISTER_API_URL}{path}"
    try:
        resp = http_requests.put(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except http_requests.exceptions.HTTPError as e:
        body = e.response.text[:200] if e.response is not None else ""
        code = e.response.status_code if e.response is not None else "?"
        print(f"  ⚠️  API PUT {path} → HTTP {code}: {body}")
    except Exception as e:
        print(f"  ⚠️  API PUT {path} failed: {e}")
    return None


def _api_get(path: str) -> Optional[Dict]:
    """GET from the Geister API. Returns parsed response or None."""
    url = f"{GEISTER_API_URL}{path}"
    try:
        resp = http_requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except http_requests.exceptions.HTTPError as e:
        body = e.response.text[:200] if e.response is not None else ""
        code = e.response.status_code if e.response is not None else "?"
        print(f"  ⚠️  API GET {path} → HTTP {code}: {body}")
    except Exception as e:
        print(f"  ⚠️  API GET {path} failed: {e}")
    return None


def register_agent(
    agent_id: str,
    display_name: str,
    persona: str,
    realm_name: str,
    realm_id: str,
    role_label: str,
) -> bool:
    """Register a persistent agent profile on the Geister API.

    The profile (and its telos) will survive the CI run so it is visible
    on the dashboard afterwards.
    """
    metadata = {
        "source": "agent-swarm-ci",
        "run_id": RUN_ID,
        "role_label": role_label,
        "realm_name": realm_name,
        "realm_id": realm_id,
        "network": NETWORK,
        "registered_at": datetime.now().isoformat(),
    }
    if GITHUB_REPOSITORY:
        metadata["github_repository"] = GITHUB_REPOSITORY

    result = _api_post("/api/agents", {
        "agent_id": agent_id,
        "display_name": display_name,
        "persona": persona,
        "metadata": metadata,
    })
    return result is not None and result.get("success", False)


def update_agent_after_run(agent_id: str, success: bool, issues: List[str], error: Optional[str] = None) -> None:
    """Update the agent's metadata with run results so they are visible on the dashboard."""
    metadata_patch: Dict = {
        "last_run_id": RUN_ID,
        "last_run_success": success,
        "last_run_at": datetime.now().isoformat(),
    }
    if issues:
        metadata_patch["last_run_issues"] = issues[:10]  # cap to avoid huge payloads
    if error:
        metadata_patch["last_run_error"] = str(error)[:500]
    _api_put(f"/api/agents/{agent_id}", {"metadata": metadata_patch})


def register_all_agents(tasks: List[Tuple]) -> int:
    """Register all agents with the Geister API. Returns count of successes."""
    print(f"\nRegistering {len(tasks)} agent(s) on {GEISTER_API_URL} (run_id={RUN_ID})...")
    registered = 0
    for agent_id, slot, realm_id, realm_name in tasks:
        ok = register_agent(
            agent_id=agent_id,
            display_name=agent_id,  # the API will generate a human name if it's a swarm_agent_NNN id
            persona=slot.persona,
            realm_name=realm_name,
            realm_id=realm_id,
            role_label=slot.role_label,
        )
        if ok:
            registered += 1
            print(f"  ✅ {agent_id} [{slot.role_label}] → {realm_name}")
        else:
            print(f"  ⚠️  {agent_id} [{slot.role_label}] → registration failed (will still attempt run)")
    print(f"Registered {registered}/{len(tasks)} agents\n")
    return registered


def assign_telos_to_all(tasks: List[Tuple]) -> int:
    """Assign telos to each agent, using DB templates when available. Returns count of successes."""
    print(f"Assigning telos to {len(tasks)} agent(s)...")

    # Fetch available templates once so we can resolve template_name → template_id
    templates_by_name: Dict[str, int] = {}
    templates_resp = _api_get("/api/telos/templates")
    if templates_resp and templates_resp.get("templates"):
        for t in templates_resp["templates"]:
            templates_by_name[t["name"]] = t["id"]
        print(f"  Templates available: {list(templates_by_name.keys())}")

    assigned = 0
    for agent_id, slot, realm_id, realm_name in tasks:
        payload: Dict = {}
        if slot.template_name and slot.template_name in templates_by_name:
            payload["template_id"] = templates_by_name[slot.template_name]
        else:
            payload["custom_telos"] = slot.task
        result = _api_put(f"/api/agents/{agent_id}/telos", payload)
        if result and result.get("success"):
            telos_src = slot.template_name or "custom"
            print(f"  ✅ {agent_id} [{slot.role_label}] → telos: {telos_src}")
            assigned += 1
        else:
            print(f"  ⚠️  {agent_id}: telos assignment failed")
    print(f"Assigned telos to {assigned}/{len(tasks)} agents\n")
    return assigned


def activate_all_agents() -> int:
    """Set all agents to telos_state='active'. Returns updated count."""
    result = _api_put("/api/agents/telos/state", {"state": "active"})
    count = result.get("updated_count", 0) if result else 0
    print(f"Activated {count} agent(s)")
    return count


def ensure_executor_running() -> bool:
    """Start the telos executor if not already running."""
    status = _api_get("/api/telos/executor/status")
    if status and status.get("running"):
        print("Telos executor already running")
        return True
    result = _api_post("/api/telos/executor/start", {})
    if result and result.get("success"):
        print("Telos executor started")
        return True
    print("  ⚠️  Could not start telos executor")
    return False


def poll_until_complete(
    agent_ids: List[str],
    deadline: float,
    poll_interval: int = 5,
) -> Dict[str, Dict]:
    """
    Poll the Geister API until all agents reach a terminal telos state
    (completed / failed) or the deadline is exceeded.

    Returns a dict mapping agent_id → agent data (including telos info).
    """
    terminal = {"completed", "failed"}
    final: Dict[str, Dict] = {}

    while time.time() < deadline:
        data = _api_get("/api/agents")
        if not data or not data.get("success"):
            time.sleep(poll_interval)
            continue

        agents_by_id = {a["agent_id"]: a for a in data.get("agents", [])}

        pending = []
        for aid in agent_ids:
            agent = agents_by_id.get(aid)
            if not agent:
                continue
            state = agent.get("telos_state") or "idle"
            if state in terminal:
                if aid not in final:
                    final[aid] = agent
                    emoji = "✅" if state == "completed" else "❌"
                    print(f"  {emoji} {aid} → {state}")
            else:
                pending.append(aid)

        if not pending:
            print(f"\nAll {len(agent_ids)} agents reached terminal state")
            break

        # Show progress
        done = len(final)
        total = len(agent_ids)
        remaining = int(deadline - time.time())
        sys.stdout.write(f"\r  ⏳ {done}/{total} done, {len(pending)} pending, {remaining}s remaining  ")
        sys.stdout.flush()

        time.sleep(poll_interval)
    else:
        print(f"\n⏰ Deadline reached with {len(agent_ids) - len(final)} agent(s) still pending")
        # Collect whatever state the remaining agents are in
        data = _api_get("/api/agents")
        if data and data.get("success"):
            for a in data.get("agents", []):
                if a["agent_id"] in agent_ids and a["agent_id"] not in final:
                    final[a["agent_id"]] = a

    print()  # newline after progress line
    return final


def collect_agent_results(
    agent_ids: List[str],
    final_states: Dict[str, Dict],
    tasks: List[Tuple],
) -> List[Dict]:
    """Fetch telos results for each agent and build log entries."""
    logs = []
    task_map = {agent_id: (slot, realm_id, realm_name) for agent_id, slot, realm_id, realm_name in tasks}

    for agent_id in agent_ids:
        slot, realm_id, realm_name = task_map[agent_id]
        agent = final_states.get(agent_id, {})
        telos_state = agent.get("telos_state", "unknown")

        # Fetch telos progress (contains step results with LLM responses)
        telos_data = _api_get(f"/api/agents/{agent_id}/telos")
        step_results = {}
        response_text = ""
        if telos_data and telos_data.get("success"):
            telos = telos_data.get("telos") or {}
            step_results = telos.get("step_results") or {}
            # Extract response from the latest step result
            if step_results:
                latest_key = max(step_results.keys(), key=lambda k: int(k) if k.isdigit() else -1)
                latest_step = step_results.get(latest_key) or {}
                response_text = latest_step.get("result") or latest_step.get("error") or ""

        success = telos_state == "completed"
        error = None
        if telos_state == "failed":
            if step_results:
                fail_key = max(step_results.keys(), key=lambda k: int(k) if k.isdigit() else -1)
                fail_step = step_results.get(fail_key) or {}
                error = fail_step.get("result") or fail_step.get("error") or "Step failed"
            else:
                error = "Step failed"
        elif telos_state not in ("completed", "failed"):
            error = f"Agent did not finish (state={telos_state})"

        issues = extract_issues(response_text)

        log_entry = {
            "agent_id": agent_id,
            "realm_id": realm_id,
            "realm_name": realm_name,
            "persona": slot.persona,
            "role_label": slot.role_label,
            "timestamp": datetime.now().isoformat(),
            "telos_state": telos_state,
            "parsed_response": {
                "success": success,
                "agent_id": agent_id,
                "realm": realm_id,
                "question": slot.task,
                "response": response_text,
                "error": error,
            },
            "success": success,
            "error": error,
            "issues": issues,
        }
        logs.append(log_entry)

    return logs


# ---------------------------------------------------------------------------
# Realm discovery
# ---------------------------------------------------------------------------

def discover_realms() -> List[Dict[str, str]]:
    """Discover realms from the registry via the realms CLI."""
    print(f"Discovering realms from registry (network: {NETWORK})...")
    try:
        result = subprocess.run(
            ["realms", "registry", "realm", "list", "--network", NETWORK],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"Error listing registry: {result.stderr}")
            return []

        realms = []
        records = re.findall(r"record\s*\{([^}]+)\}", result.stdout, re.DOTALL)
        for record in records:
            realm: Dict[str, str] = {}
            id_m = re.search(r'id\s*=\s*"([^"]+)"', record)
            name_m = re.search(r'name\s*=\s*"([^"]+)"', record)
            url_m = re.search(r'url\s*=\s*"([^"]+)"', record)
            if id_m:
                realm["id"] = id_m.group(1)
            if name_m:
                realm["name"] = name_m.group(1)
            if url_m:
                realm["url"] = url_m.group(1)
            if realm.get("id") and realm.get("name"):
                realms.append(realm)
        return realms

    except subprocess.TimeoutExpired:
        print("Timeout discovering realms")
        return []
    except Exception as e:
        print(f"Error discovering realms: {e}")
        return []


# ---------------------------------------------------------------------------
# Issue extraction
# ---------------------------------------------------------------------------

def extract_issues(text: str) -> List[str]:
    """Extract ISSUE: lines from agent response text."""
    issues = []
    for line in text.splitlines():
        m = re.match(r"\s*ISSUE:\s*(.+)", line, re.IGNORECASE)
        if m:
            issue_text = m.group(1).strip()
            if issue_text:
                issues.append(issue_text)
    return issues


# ---------------------------------------------------------------------------
# GitHub issue creation
# ---------------------------------------------------------------------------

def file_github_issue(title: str, body: str) -> bool:
    """File a GitHub issue using `gh issue create`. Returns True on success."""
    if not GITHUB_REPOSITORY or not GH_TOKEN:
        print(f"  ⚠️  Skipping GitHub issue creation (no GITHUB_REPOSITORY or GH_TOKEN)")
        return False
    try:
        env = {**os.environ, "GH_TOKEN": GH_TOKEN}
        result = subprocess.run(
            [
                "gh", "issue", "create",
                "--repo", GITHUB_REPOSITORY,
                "--title", f"[agent-swarm] {title[:120]}",
                "--body", body[:65000],  # GH has a body size limit
                "--label", ISSUE_LABEL,
            ],
            capture_output=True, text=True, timeout=30, env=env,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            print(f"  📝 Filed GitHub issue: {url}")
            return True
        else:
            print(f"  ⚠️  gh issue create failed: {result.stderr.strip()}")
            return False
    except Exception as e:
        print(f"  ⚠️  Error filing GitHub issue: {e}")
        return False


# ---------------------------------------------------------------------------
# Swarm runner
# ---------------------------------------------------------------------------

def build_task_list(realms: List[Dict]) -> List[Tuple]:
    """
    Build a flat task list across all realms with the distribution:
      - 1 ashoka (assistant) — assigned to the first realm
      - 1 founder — assigned to the first realm
      - All remaining slots: cycle equally through CITIZEN_PERSONAS
    """
    tasks = []
    agent_counter = 0
    ashoka_assigned = False
    founder_assigned = False
    citizen_idx = 0

    for realm in realms:
        for _i in range(AGENTS_COUNT):
            if not ashoka_assigned:
                slot = SLOT_ASHOKA
                ashoka_assigned = True
            elif not founder_assigned:
                slot = SLOT_FOUNDER
                founder_assigned = True
            else:
                slot = CITIZEN_PERSONAS[citizen_idx % len(CITIZEN_PERSONAS)]
                citizen_idx += 1
            agent_counter += 1
            agent_id = f"swarm_agent_{agent_counter:03d}"
            tasks.append((agent_id, slot, realm["id"], realm["name"]))
    return tasks


def run_agent_swarm(realms: List[Dict]) -> Dict:
    """Run the full agent swarm via the telos executor and return aggregated results.

    Flow:
      1. Build task list (1 ashoka + 1 founder + cycling citizens)
      2. Register agent profiles on the Geister API
      3. Assign a custom telos (the task text) to each agent
      4. Activate all agents + ensure the telos executor is running
      5. Poll until all agents complete/fail or the time budget runs out
      6. Collect results from the API and save logs
    """
    results: Dict = {
        "total_success": 0,
        "total_failed": 0,
        "total_issues": 0,
        "realms": {},
        "all_issues": [],
    }

    for realm in realms:
        results["realms"][realm["id"]] = {
            "name": realm["name"],
            "id": realm["id"],
            "success": 0,
            "failed": 0,
            "agents": [],
        }

    tasks = build_task_list(realms)

    # Register persistent agent profiles on the Geister API
    register_all_agents(tasks)

    print(f"\nRunning {len(tasks)} agents across {len(realms)} realm(s)...")
    print(f"(AGENTS_COUNT={AGENTS_COUNT}/realm, "
          f"RUN_DURATION_MINUTES={RUN_DURATION_MINUTES or 'unlimited'})")
    print("=" * 60)

    # Log persona distribution
    from collections import Counter
    persona_dist = Counter(slot.persona for _, slot, _, _ in tasks)
    for persona, count in sorted(persona_dist.items()):
        print(f"  {count:3d} × {persona}")
    print("=" * 60)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # --- Assign telos & activate ---
    assign_telos_to_all(tasks)
    activate_all_agents()
    ensure_executor_running()

    # --- Poll until all agents reach a terminal state ---
    overall_deadline = (
        time.time() + RUN_DURATION_MINUTES * 60
        if RUN_DURATION_MINUTES > 0
        else time.time() + 3600  # fallback: 1 hour
    )
    agent_ids = [aid for aid, *_ in tasks]
    final_states = poll_until_complete(agent_ids, overall_deadline)

    # --- Collect results ---
    all_logs = collect_agent_results(agent_ids, final_states, tasks)

    for log_entry in all_logs:
        agent_id = log_entry["agent_id"]
        realm_id = log_entry["realm_id"]
        realm_name = log_entry["realm_name"]
        slot_info = next((s for a, s, r, rn in tasks if a == agent_id), None)

        realm_result = results["realms"][realm_id]

        # Save per-agent log
        safe_label = log_entry["role_label"].replace(" ", "_")
        log_file = LOGS_DIR / f"{agent_id}_{realm_name.replace(' ', '_')}_{safe_label}.json"
        with open(log_file, "w") as f:
            json.dump(log_entry, f, indent=2)

        if log_entry["success"]:
            realm_result["success"] += 1
            results["total_success"] += 1
            issue_count = len(log_entry["issues"])
            issue_note = f"  ({issue_count} issue(s) found)" if issue_count else ""
            emoji = slot_info.emoji if slot_info else "❓"
            print(f"  {emoji} ✅ {agent_id} [{log_entry['role_label']}] → {realm_name}{issue_note}")
        else:
            realm_result["failed"] += 1
            results["total_failed"] += 1
            err = (log_entry.get("error") or "unknown error")[:80]
            emoji = slot_info.emoji if slot_info else "❓"
            print(f"  {emoji} ❌ {agent_id} [{log_entry['role_label']}] → {realm_name}: {err}")

        # Update the persistent agent profile with run results
        update_agent_after_run(agent_id, log_entry["success"], log_entry["issues"], log_entry.get("error"))

        realm_result["agents"].append({
            "agent_id": agent_id,
            "role_label": log_entry["role_label"],
            "persona": log_entry["persona"],
            "success": log_entry["success"],
            "issues": log_entry["issues"],
        })

        # Accumulate issues
        for issue_text in log_entry["issues"]:
            results["total_issues"] += 1
            results["all_issues"].append({
                "agent_id": agent_id,
                "role_label": log_entry["role_label"],
                "realm_name": realm_name,
                "realm_id": realm_id,
                "description": issue_text,
                "log_file": str(log_file),
            })

    # Save combined log
    combined = LOGS_DIR / "all_agents.json"
    with open(combined, "w") as f:
        json.dump(all_logs, f, indent=2)
    print(f"\n📋 Agent logs saved to {LOGS_DIR}/")

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_summary(results: Dict) -> None:
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for realm_id, realm in results["realms"].items():
        total = realm["success"] + realm["failed"]
        pct = 100 * realm["success"] / total if total > 0 else 0
        print(f"  {realm['name']}: {realm['success']}/{total} successful ({pct:.0f}%)")

    total = results["total_success"] + results["total_failed"]
    if total > 0:
        pct = 100 * results["total_success"] / total
        print(f"\n  TOTAL: {results['total_success']}/{total} successful ({pct:.1f}%)")
    else:
        print("\n  TOTAL: No agents ran")

    issue_count = results["total_issues"]
    if issue_count:
        print(f"\n  ⚠️  {issue_count} issue(s) reported by agents:")
        for i, issue in enumerate(results["all_issues"], 1):
            print(f"    {i}. [{issue['realm_name']} / {issue['role_label']}] {issue['description']}")
    else:
        print("\n  ✅ No issues reported by agents")


def write_issues_report(results: Dict) -> Path:
    """Write a human-readable issues-report.md file."""
    report_path = LOGS_DIR / "issues-report.md"
    lines = ["# Agent Swarm Issues Report\n"]
    lines.append(f"**Run:** {datetime.now().isoformat()}\n")
    lines.append(f"**Run ID:** {RUN_ID}\n")
    lines.append(f"**Network:** {NETWORK}\n")
    lines.append(f"**Agents:** {results['total_success'] + results['total_failed']}\n\n")

    if results["all_issues"]:
        lines.append(f"## Issues Found ({results['total_issues']})\n\n")
        for i, issue in enumerate(results["all_issues"], 1):
            lines.append(
                f"### Issue {i}: {issue['description']}\n"
                f"- **Realm:** {issue['realm_name']} (`{issue['realm_id']}`)\n"
                f"- **Agent:** {issue['agent_id']} ({issue['role_label']})\n"
                f"- **Log:** `{issue['log_file']}`\n\n"
            )
    else:
        lines.append("## No issues found ✅\n")

    with open(report_path, "w") as f:
        f.writelines(lines)

    print(f"📄 Issues report saved to {report_path}")
    return report_path


def file_discovered_issues(results: Dict) -> int:
    """
    File a GitHub issue for each agent-reported issue.
    Returns the number of issues successfully filed.
    """
    if not results["all_issues"]:
        return 0

    if not GITHUB_REPOSITORY or not GH_TOKEN:
        print("\n⚠️  GitHub issue filing skipped (set GITHUB_REPOSITORY and GH_TOKEN/GITHUB_TOKEN to enable)")
        return 0

    print(f"\n📝 Filing {len(results['all_issues'])} GitHub issue(s)...")
    filed = 0
    for issue in results["all_issues"]:
        title = f"[{issue['realm_name']}] {issue['description']}"

        # Load agent log for the body
        try:
            with open(issue["log_file"]) as f:
                log_data = json.load(f)
            response_text = (log_data.get("parsed_response") or {}).get("response", "")
            body = (
                f"**Discovered by agent swarm** during post-deployment staging test.\n\n"
                f"**Realm:** {issue['realm_name']} (`{issue['realm_id']}`)\n"
                f"**Agent:** {issue['agent_id']} (persona: `{issue['role_label']}`)\n"
                f"**Network:** {NETWORK}\n\n"
                f"---\n\n"
                f"**Issue reported:**\n> {issue['description']}\n\n"
                f"**Full agent response:**\n```\n{response_text[:10000]}\n```\n"
            )
        except Exception as e:
            body = (
                f"**Discovered by agent swarm** during post-deployment staging test.\n\n"
                f"**Realm:** {issue['realm_name']} (`{issue['realm_id']}`)\n"
                f"**Agent:** {issue['agent_id']} (persona: `{issue['role_label']}`)\n"
                f"**Network:** {NETWORK}\n\n"
                f"**Issue reported:**\n> {issue['description']}\n\n"
                f"_(Could not load agent log: {e})_\n"
            )

        if file_github_issue(title, body):
            filed += 1

    return filed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("GEISTER AGENT SWARM TEST")
    print("=" * 60)
    print(f"API URL:          {GEISTER_API_URL}")
    print(f"Network:          {NETWORK}")
    print(f"Run ID:           {RUN_ID}")
    print(f"Agents:           {AGENTS_COUNT}/realm")
    dur = f"{RUN_DURATION_MINUTES}min" if RUN_DURATION_MINUTES > 0 else "unlimited"
    print(f"Overall budget:   {dur}")
    print(f"GH issue filing:  {'enabled' if GITHUB_REPOSITORY and GH_TOKEN else 'disabled'}")

    # Discover realms
    realms = discover_realms()
    if not realms:
        print("\n❌ No realms found in registry")
        sys.exit(1)

    print(f"\nFound {len(realms)} realm(s):")
    for realm in realms:
        print(f"  - {realm['name']} ({realm['id']})")

    # Run swarm
    results = run_agent_swarm(realms)

    # Print summary
    print_summary(results)

    # Write issues report artifact
    write_issues_report(results)

    # File GitHub issues for discovered problems
    gh_filed = file_discovered_issues(results)
    if gh_filed:
        print(f"\n✅ Filed {gh_filed} GitHub issue(s)")

    # Check failure threshold
    total = results["total_success"] + results["total_failed"]
    if total > 0:
        failure_rate = results["total_failed"] / total
        if failure_rate > FAILURE_THRESHOLD:
            print(f"\n❌ Failure rate ({failure_rate:.1%}) exceeds threshold ({FAILURE_THRESHOLD:.0%})")
            sys.exit(1)

    print("\n✅ Agent swarm test passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
