#!/usr/bin/env python3
"""
Post-deployment script for a realm.
Handles realm registration, runs canister_init.py if present, and reloads entity overrides.
"""

import subprocess
import os
import sys
import json
import time

# Determine working directory (can be overridden via REALM_DIR env var)
# Default to current working directory (where script is invoked from)
script_dir = os.path.dirname(os.path.abspath(__file__))
realm_dir = os.environ.get('REALM_DIR', os.getcwd())
os.chdir(realm_dir)

# Network from env var or command line arg
network = os.environ.get('NETWORK') or (sys.argv[1] if len(sys.argv) > 1 else 'local')
# mode = sys.argv[2] if len(sys.argv) > 2 else 'upgrade'  # Not used by this script
print(f"🚀 Running post-deployment tasks for network: {network}")


PORTAL_HOSTS = {
    "staging": "https://staging.gos.earth",
    "demo": "https://demo.gos.earth",
    "test": "https://test.gos.earth",
    "ic": "https://realmsgos.org",
    "production": "https://realmsgos.org",
}


def _slugify(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (name or "realm").lower()).strip("-")
    return (s[:48] or "realm")


def _claim_federation_slug(
    manifest, realm_name, network, registry_canister_id,
    frontend_id, backend_id, realm_dir,
):
    """Register federation slug on realm_registry_backend after deploy.

    The registry only lets the installer or a controller create a new slug, so
    on staging and production this call is refused (a warning below) unless the
    dfx identity is a registry controller. GaaS deployments get their slug from
    the installer.
    """
    if not (frontend_id and backend_id and registry_canister_id):
        return
    federation = manifest.get("federation") or {}
    slug = federation.get("slug") or _slugify(realm_name)
    portal_base = PORTAL_HOSTS.get(network, PORTAL_HOSTS["staging"])
    portal_url = federation.get("portal_url") or f"{portal_base}/r/{slug}"
    if portal_url.startswith("http"):
        portal_base = portal_url.rsplit("/r/", 1)[0]
    registry_backend = os.environ.get("REGISTRY_BACKEND_CANISTER", "realm_registry_backend")
    claim_args = f'("{slug}", "{frontend_id}", "{backend_id}", "{portal_base}", "")'
    print(f"   Claiming federation slug '{slug}' → {portal_url}")
    try:
        result = subprocess.run(
            [
                "dfx", "canister", "call", registry_backend, "claim_slug",
                claim_args, "--network", network,
            ],
            cwd=realm_dir, capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and "Ok" in result.stdout:
            print(f"   ✅ Slug '{slug}' claimed")
        else:
            print(f"   ⚠️  claim_slug: {result.stdout or result.stderr}")
    except Exception as e:
        print(f"   ⚠️  claim_slug failed: {e}")


def run_command(cmd, capture=True):
    """Run a command and return the result."""
    print(f"Running: {' '.join(cmd)}")
    if capture:
        result = subprocess.run(cmd, cwd=realm_dir, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Command failed: {result.stderr}")
        return result.stdout.strip()
    else:
        result = subprocess.run(cmd, cwd=realm_dir)
        if result.returncode != 0:
            raise Exception(f"Command failed with code {result.returncode}")
        return None


# Detect backend canister name from dfx.json
backend_name = "realm_backend"
try:
    with open("dfx.json", "r") as f:
        dfx_config = json.load(f)
    canister_names = list(dfx_config.get("canisters", {}).keys())
    # Prioritize realm_backend specifically, then fall back to first *_backend (excluding token_backend)
    if "realm_backend" in canister_names:
        backend_name = "realm_backend"
    else:
        for name in canister_names:
            if name.endswith("_backend") and name not in ("realm_registry_backend", "token_backend"):
                backend_name = name
                break
except Exception:
    pass
print(f"🎯 Target canister: {backend_name}")


# Register realm with registry (if not already registered)
try:
    print(f"\n🌐 Checking realm registration...")
    
    # Load manifest to get realm name
    manifest_path = os.path.join(realm_dir, 'manifest.json')
    if os.path.exists(manifest_path):
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
        realm_name = manifest.get('name', 'Generated Realm')
        
        # Generate a unique realm ID based on name and timestamp
        realm_id = f"{realm_name.lower().replace(' ', '_')}_{int(time.time())}"
        
        print(f"   Realm Name: {realm_name}")
        print(f"   Realm ID: {realm_id}")
        print(f"   Network: {network}")
        
        # Get frontend and backend canister IDs
        frontend_url = ""
        backend_url = ""
        backend_id = ""
        frontend_id = ""
        
        # Detect dfx webserver port for local network
        local_port = 8000
        if network == 'local':
            try:
                port_result = subprocess.run(
                    ['dfx', 'info', 'webserver-port'],
                    capture_output=True, text=True, timeout=5, cwd=realm_dir
                )
                if port_result.returncode == 0 and port_result.stdout.strip():
                    local_port = int(port_result.stdout.strip())
            except Exception:
                pass
        
        try:
            # Find canister names from dfx.json
            dfx_json_path = os.path.join(realm_dir, 'dfx.json')
            frontend_name = "realm_frontend"
            backend_name_local = "realm_backend"
            if os.path.exists(dfx_json_path):
                with open(dfx_json_path, 'r') as f:
                    dfx_config = json.load(f)
                canister_names = list(dfx_config.get("canisters", {}).keys())
                # Prioritize realm_backend specifically, then fall back to first *_backend
                if "realm_backend" in canister_names:
                    backend_name_local = "realm_backend"
                else:
                    for name in canister_names:
                        if name.endswith("_backend") and name not in ("realm_registry_backend", "token_backend"):
                            backend_name_local = name
                            break
                # Same for frontend
                if "realm_frontend" in canister_names:
                    frontend_name = "realm_frontend"
                else:
                    for name in canister_names:
                        if name.endswith("_frontend") and name != "realm_registry_frontend":
                            frontend_name = name
                            break
            
            # Get frontend canister ID
            result = subprocess.run(
                ['dfx', 'canister', 'id', frontend_name, '--network', network],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                frontend_id = result.stdout.strip()
                if network == 'ic':
                    frontend_url = f"{frontend_id}.ic0.app"
                elif network in ('staging', 'demo', 'test'):
                    frontend_url = f"{frontend_id}.icp0.io"
                else:  # local
                    frontend_url = f"{frontend_id}.localhost:{local_port}"
                print(f"   Frontend URL: {frontend_url}")
            
            # Get backend canister ID
            result = subprocess.run(
                ['dfx', 'canister', 'id', backend_name_local, '--network', network],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                backend_id = result.stdout.strip()
                if network == 'ic':
                    backend_url = f"{backend_id}.ic0.app"
                elif network in ('staging', 'demo', 'test'):
                    backend_url = f"{backend_id}.icp0.io"
                else:  # local
                    backend_url = f"{backend_id}.localhost:{local_port}"
                print(f"   Backend URL: {backend_url}")
        except Exception as e:
            print(f"   ⚠️  Could not get canister URLs: {e}")
        
        # Register realm via inter-canister call from realm_backend to registry
        # The registry uses ic.caller() (realm_backend's canister ID) as the unique key
        # This prevents duplicates - same canister calling again just updates the record
        # Read registry canister ID from manifest services section, fall back to env var
        registry_canister_id = manifest.get('services', {}).get('registry', {}).get('canister_id')
        if not registry_canister_id:
            registry_canister_id = os.environ.get('REGISTRY_CANISTER_ID')
        
        if registry_canister_id:
            print(f"   Registering realm with central registry...")
            
            # Call realm_backend's register_realm function which makes inter-canister call
            # Signature: register_realm_with_registry(registry_canister_id, realm_name, frontend_url, canister_ids_packed)
            # canister_ids_packed is pipe-delimited: frontend_id|token_id|nft_id (JSON triggers basilisk Candid bug)
            canister_ids_packed = "||"  # frontend|token|nft - empty for now
            register_args = f'("{registry_canister_id}", "{realm_name}", "{frontend_url}", "{canister_ids_packed}")'
            register_cmd = [
                'dfx', 'canister', 'call', backend_name_local, 'register_realm_with_registry',
                register_args,
                '--network', network
            ]
            
            register_result = subprocess.run(register_cmd, cwd=realm_dir, capture_output=True, text=True)
            if register_result.returncode == 0:
                # Check actual response content, not just returncode
                response = register_result.stdout.strip()
                if '"success": true' in response.lower() or '"success":true' in response.lower():
                    print(f"   ✅ Realm registered successfully!")
                    print(f"   Response: {response[:200]}..." if len(response) > 200 else f"   Response: {response}")
                    _claim_federation_slug(
                        manifest, realm_name, network, registry_canister_id,
                        frontend_id, backend_id, realm_dir,
                    )
                else:
                    print(f"   ⚠️  Registration may have failed. Response: {response[:300]}")
                    # Continue to fallback
            else:
                # Fallback: Try direct CLI registration if inter-canister call fails
                print(f"   ⚠️  Inter-canister registration failed, trying direct registration...")
                register_cmd = ['realms', 'registry', 'add', 
                               '--realm-id', backend_id if backend_id else realm_id,
                               '--realm-name', realm_name,
                               '--network', network,
                               '--registry-canister', registry_canister_id]
                if frontend_url:
                    register_cmd.extend(['--frontend-url', frontend_url])
                if backend_url:
                    register_cmd.extend(['--backend-url', backend_url])
                register_result = subprocess.run(register_cmd, cwd=realm_dir)
                if register_result.returncode == 0:
                    print(f"   ✅ Realm registered successfully (direct)!")
                else:
                    print(f"   ⚠️  Failed to register realm (continuing anyway)")
        else:
            print(f"   ℹ️  No registry canister ID provided, skipping registration")
    else:
        print(f"   ⚠️  No manifest.json found, skipping registration")
except Exception as e:
    print(f"   ⚠️  Could not register realm: {e} (continuing anyway)")


# Run canister initialization script if present
canister_init_path = os.path.join(script_dir, 'canister_init.py')
if os.path.exists(canister_init_path):
    print(f"\n📝 Running canister initialization script...")
    realms_cmd = ['realms', 'shell', '--file', canister_init_path, '--canister', backend_name]
    if network != 'local':
        realms_cmd.extend(['--network', network])
    try:
        run_command(realms_cmd, capture=False)
        print(f"   ✅ Canister initialization completed")
    except Exception as e:
        print(f"   ⚠️  Canister initialization failed: {e} (continuing anyway)")
else:
    print(f"\nℹ️  No canister_init.py found, skipping initialization...")

# Update realm config from manifest.json
manifest_path = os.path.join(realm_dir, 'manifest.json')
if os.path.exists(manifest_path):
    print(f"\n🔧 Updating realm config from manifest.json...")
    try:
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
        
        config = {
            "name": manifest.get("name", ""),
            "manifesto": manifest.get("manifesto", ""),
            "welcome_message": manifest.get("welcome_message", ""),
            "open_registration": manifest.get("open_registration", False),
        }
        # Use ensure_ascii=False to keep UTF-8 characters instead of \uXXXX escapes
        # which dfx Candid parser doesn't understand
        config_json = json.dumps(config, ensure_ascii=False).replace('"', '\\"')
        
        update_cmd = ['dfx', 'canister', 'call', backend_name, 'update_realm_config', f'("{config_json}")']
        if network != 'local':
            update_cmd.extend(['--network', network])
        result = run_command(update_cmd)
        print(f"   ✅ Realm config updated: {result}")
    except Exception as e:
        print(f"   ⚠️  Failed to update realm config: {e} (continuing anyway)")
else:
    print(f"\nℹ️  No manifest.json found, skipping realm config update...")


# Register quarter backends with the main backend
try:
    print(f"\n🏘️  Checking for quarter backends to register...")
    
    # Load manifest to check quarters count
    manifest_path = os.path.join(realm_dir, 'manifest.json')
    num_quarters = 1
    if os.path.exists(manifest_path):
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
        num_quarters = manifest.get('quarters', 1)
    
    if num_quarters > 1:
        # Load dfx.json to find quarter backend canister names
        dfx_json_path = os.path.join(realm_dir, 'dfx.json')
        if os.path.exists(dfx_json_path):
            with open(dfx_json_path, 'r') as f:
                dfx_config = json.load(f)
            
            quarter_canisters = [
                name for name in dfx_config.get("canisters", {}).keys()
                if name.startswith("quarter_") and name.endswith("_backend")
            ]
            
            for q_canister in quarter_canisters:
                try:
                    # Get the quarter's canister ID
                    result = subprocess.run(
                        ['dfx', 'canister', 'id', q_canister, '--network', network],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode != 0:
                        print(f"   ⚠️  Could not get canister ID for {q_canister}: {result.stderr}")
                        continue
                    
                    quarter_canister_id = result.stdout.strip()
                    # Derive a human-readable name from canister name: quarter_1_backend -> Quarter 1
                    q_index = q_canister.replace("quarter_", "").replace("_backend", "")
                    quarter_name = f"Quarter {q_index}"
                    
                    print(f"   Registering {quarter_name} ({quarter_canister_id}) on main backend...")
                    
                    register_args = f'("{quarter_name}", "{quarter_canister_id}")'
                    register_cmd = [
                        'dfx', 'canister', 'call', backend_name, 'register_quarter',
                        register_args,
                        '--network', network
                    ]
                    reg_result = subprocess.run(register_cmd, cwd=realm_dir, capture_output=True, text=True)
                    if reg_result.returncode == 0:
                        print(f"   ✅ Registered {quarter_name} (canister: {quarter_canister_id})")
                    else:
                        print(f"   ⚠️  Failed to register {quarter_name}: {reg_result.stderr}")
                except Exception as qe:
                    print(f"   ⚠️  Error registering {q_canister}: {qe}")
        else:
            print(f"   ⚠️  No dfx.json found, cannot detect quarter canisters")
    else:
        print(f"   ℹ️  No quarters configured (quarters={num_quarters}), skipping")
except Exception as e:
    print(f"   ⚠️  Quarter registration failed: {e} (continuing anyway)")


# Import manifest.json as a codex (contains entity_method_overrides)
manifest_path = os.path.join(realm_dir, 'manifest.json')
if os.path.exists(manifest_path):
    print(f"\n📜 Importing manifest.json as codex...")
    import_cmd = ['realms', 'import', manifest_path, '--type', 'codex', '--canister', backend_name]
    if network != 'local':
        import_cmd.extend(['--network', network])
    try:
        run_command(import_cmd, capture=False)
        print(f"   ✅ Manifest imported as codex")
    except Exception as e:
        print(f"   ⚠️  Failed to import manifest: {e}")
else:
    print(f"\nℹ️  No manifest.json found at {manifest_path}, skipping...")

# Reload entity method overrides after adjustments
print("\n🔄 Reloading entity method overrides...")
reload_cmd = ['dfx', 'canister', 'call', backend_name, 'reload_entity_method_overrides']
if network != 'local':
    reload_cmd.extend(['--network', network])
try:
    result = run_command(reload_cmd)
    print(f"   ✅ Entity method overrides reloaded: {result}")
except Exception as e:
    print(f"   ⚠️  Failed to reload overrides: {e}")

# Seed Token entities for Vault Manager
print("\n🪙 Seeding Token entities...")
try:
    try:
        reconcile_cmd = [
            'dfx', 'canister', 'call', backend_name, 'reconcile_treasury_token',
            '()', '--output', 'json',
        ]
        if network != 'local':
            reconcile_cmd.extend(['--network', network])
        reconcile_result = subprocess.run(
            reconcile_cmd, cwd=realm_dir, capture_output=True, text=True, timeout=120,
        )
        if reconcile_result.returncode != 0:
            print(
                f"   ⚠️  reconcile_treasury_token failed: "
                f"{reconcile_result.stderr or reconcile_result.stdout}"
            )
        else:
            outer = json.loads((reconcile_result.stdout or "").strip())
            inner = outer["Ok"] if isinstance(outer, dict) and "Ok" in outer else outer
            if isinstance(inner, dict):
                payload = inner
            elif isinstance(inner, str):
                payload = json.loads(inner)
            else:
                payload = {"success": False, "error": f"unexpected response: {inner!r}"}

            if not payload.get("success"):
                error = payload.get("error", "unknown error")
                error_code = payload.get("error_code")
                code_suffix = f" ({error_code})" if error_code else ""
                print(f"   ⚠️  Treasury token resolution failed{code_suffix}: {error}")
            elif payload.get("skipped"):
                reason = payload.get("reason", "no_treasury_ledger")
                if reason == "no_treasury_ledger":
                    print(
                        "   ℹ️  No treasury token configured yet — it will be registered "
                        "when the treasury ledger is set in Realm Settings"
                    )
                else:
                    print(f"   ℹ️  Treasury token skipped: {reason}")
            else:
                symbol = payload.get("symbol", "?")
                ledger = payload.get("ledger", "?")
                print(f"   ✅ Treasury token reconciled: {symbol} ({ledger})")
    except Exception as treasury_err:
        print(f"   ⚠️  Treasury token reconcile failed: {treasury_err}")

    tokens_to_seed = []
    realms_token_id = (os.environ.get('REALMS_TOKEN_CANISTER_ID') or "").strip()
    if realms_token_id:
        tokens_to_seed.append({
            "symbol": "REALMS",
            "name": "REALMS Token",
            "ledger_canister_id": realms_token_id,
            "indexer_canister_id": realms_token_id,
            "decimals": 8,
            "token_type": "shared",
            "enabled": "false",
        })
        print(f"   ℹ️  Including shared REALMS token (disabled): {realms_token_id}")
    else:
        print(f"   ℹ️  REALMS_TOKEN_CANISTER_ID not set, skipping shared REALMS token")

    print(f"   📊 Tokens to seed: {len(tokens_to_seed)}")
    
    # Seed each token via execute_code (since upsert_object doesn't exist)
    # Token is now the Basilisk OS Token (re-exported by GGG) with fields:
    #   name (alias), ledger, indexer, decimals, fee + GGG extensions: symbol, token_type, enabled
    for token in tokens_to_seed:
        # Build Python code to create/update the Token entity
        python_code = f'''
from ggg import Token
# Alias lookup: Token.__alias__ = "name" (Basilisk OS Token)
t = Token["{token['symbol']}"]
if t:
    t.ledger = "{token['ledger_canister_id']}"
    t.indexer = "{token['indexer_canister_id']}"
    t.decimals = {token['decimals']}
    t.symbol = "{token['symbol']}"
    t.token_type = "{token['token_type']}"
    t.enabled = "{token['enabled']}"
    "updated"
else:
    t = Token(
        name="{token['symbol']}",
        ledger="{token['ledger_canister_id']}",
        indexer="{token['indexer_canister_id']}",
        decimals={token['decimals']},
    )
    t.symbol = "{token['symbol']}"
    t.token_type = "{token['token_type']}"
    t.enabled = "{token['enabled']}"
    "created"
'''
        # Escape the code for shell
        escaped_code = python_code.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        seed_cmd = ['dfx', 'canister', 'call', backend_name, '__shell__', f'("{escaped_code}")']
        if network != 'local':
            seed_cmd.extend(['--network', network])
        result = subprocess.run(seed_cmd, cwd=realm_dir, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"   ✅ Seeded token: {token['symbol']} ({token['name']})")
        else:
            print(f"   ⚠️  Failed to seed {token['symbol']}: {result.stderr}")

except Exception as e:
    print(f"   ⚠️  Token seeding failed: {e} (continuing anyway)")

# Seed demo accounting data (Fund, FiscalPeriod, Budget, LedgerEntry)
# Skip if NO_DEMO_DATA env var is set (--no-demo-data flag)
if os.environ.get('NO_DEMO_DATA'):
    print("\n📊 Skipping demo accounting data (--no-demo-data)")
else:
    print("\n📊 Seeding demo accounting data...")
    try:
        # Python code to create demo accounting data
        accounting_seed_code = '''
from ggg import Fund, FiscalPeriod, Budget, LedgerEntry
from ggg import FundType, FiscalPeriodStatus, BudgetStatus, EntryType, Category
from datetime import datetime
import uuid

# Check if we already have accounting data
existing_funds = list(Fund.instances())
if existing_funds:
    "already_seeded"
else:
    # Create Funds
    general_fund = Fund(
        code="GF001",
        name="General Fund",
        fund_type=FundType.GENERAL,
        description="Primary operating fund for general government activities"
    )
    
    special_fund = Fund(
        code="SF001",
        name="Infrastructure Fund",
        fund_type=FundType.SPECIAL_REVENUE,
        description="Dedicated fund for infrastructure projects"
    )
    
    capital_fund = Fund(
        code="CF001",
        name="Capital Projects Fund",
        fund_type=FundType.CAPITAL_PROJECTS,
        description="Fund for major capital acquisitions and construction"
    )
    
    # Create Fiscal Period
    current_year = datetime.now().year
    fiscal_period = FiscalPeriod(
        id=f"FY{current_year}",
        name=f"Fiscal Year {current_year}",
        start_date=f"{current_year}-01-01",
        end_date=f"{current_year}-12-31",
        status=FiscalPeriodStatus.OPEN
    )
    
    # Create Budgets
    Budget(
        id=f"BUD-TAX-{current_year}",
        name="Tax Revenue Budget",
        fund=general_fund,
        fiscal_period=fiscal_period,
        category="tax",
        budget_type="revenue",
        planned_amount=500000,
        actual_amount=425000,
        status=BudgetStatus.ADOPTED,
        description="Projected tax revenue from members"
    )
    
    Budget(
        id=f"BUD-FEE-{current_year}",
        name="Service Fees Budget",
        fund=general_fund,
        fiscal_period=fiscal_period,
        category="fee",
        budget_type="revenue",
        planned_amount=150000,
        actual_amount=162000,
        status=BudgetStatus.ADOPTED,
        description="Revenue from service fees and licenses"
    )
    
    Budget(
        id=f"BUD-PERS-{current_year}",
        name="Personnel Expenses Budget",
        fund=general_fund,
        fiscal_period=fiscal_period,
        category="personnel",
        budget_type="expense",
        planned_amount=300000,
        actual_amount=285000,
        status=BudgetStatus.ADOPTED,
        description="Salaries and benefits"
    )
    
    Budget(
        id=f"BUD-INFRA-{current_year}",
        name="Infrastructure Budget",
        fund=special_fund,
        fiscal_period=fiscal_period,
        category="capital",
        budget_type="expense",
        planned_amount=200000,
        actual_amount=175000,
        status=BudgetStatus.ADOPTED,
        description="Infrastructure maintenance and improvements"
    )
    
    # Create sample Ledger Entries (double-entry transactions)
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Transaction 1: Tax Revenue received
    tx1_id = str(uuid.uuid4())[:8]
    LedgerEntry(
        id=f"LE-{tx1_id}-1",
        transaction_id=tx1_id,
        entry_type=EntryType.ASSET,
        category=Category.CASH,
        debit=425000,
        credit=0,
        entry_date=today,
        fund=general_fund,
        fiscal_period=fiscal_period,
        description="Tax revenue received",
        tags="operating"
    )
    LedgerEntry(
        id=f"LE-{tx1_id}-2",
        transaction_id=tx1_id,
        entry_type=EntryType.REVENUE,
        category=Category.TAX,
        debit=0,
        credit=425000,
        entry_date=today,
        fund=general_fund,
        fiscal_period=fiscal_period,
        description="Tax revenue received",
        tags="operating"
    )
    
    # Transaction 2: Service fees received
    tx2_id = str(uuid.uuid4())[:8]
    LedgerEntry(
        id=f"LE-{tx2_id}-1",
        transaction_id=tx2_id,
        entry_type=EntryType.ASSET,
        category=Category.CASH,
        debit=162000,
        credit=0,
        entry_date=today,
        fund=general_fund,
        fiscal_period=fiscal_period,
        description="Service fees collected",
        tags="operating"
    )
    LedgerEntry(
        id=f"LE-{tx2_id}-2",
        transaction_id=tx2_id,
        entry_type=EntryType.REVENUE,
        category=Category.FEE,
        debit=0,
        credit=162000,
        entry_date=today,
        fund=general_fund,
        fiscal_period=fiscal_period,
        description="Service fees collected",
        tags="operating"
    )
    
    # Transaction 3: Personnel expenses paid
    tx3_id = str(uuid.uuid4())[:8]
    LedgerEntry(
        id=f"LE-{tx3_id}-1",
        transaction_id=tx3_id,
        entry_type=EntryType.EXPENSE,
        category=Category.PERSONNEL,
        debit=285000,
        credit=0,
        entry_date=today,
        fund=general_fund,
        fiscal_period=fiscal_period,
        description="Personnel salaries and benefits",
        tags="operating"
    )
    LedgerEntry(
        id=f"LE-{tx3_id}-2",
        transaction_id=tx3_id,
        entry_type=EntryType.ASSET,
        category=Category.CASH,
        debit=0,
        credit=285000,
        entry_date=today,
        fund=general_fund,
        fiscal_period=fiscal_period,
        description="Personnel salaries and benefits",
        tags="operating"
    )
    
    # Transaction 4: Infrastructure investment
    tx4_id = str(uuid.uuid4())[:8]
    LedgerEntry(
        id=f"LE-{tx4_id}-1",
        transaction_id=tx4_id,
        entry_type=EntryType.ASSET,
        category=Category.EQUIPMENT,
        debit=175000,
        credit=0,
        entry_date=today,
        fund=special_fund,
        fiscal_period=fiscal_period,
        description="Infrastructure equipment purchase",
        tags="investing,capital"
    )
    LedgerEntry(
        id=f"LE-{tx4_id}-2",
        transaction_id=tx4_id,
        entry_type=EntryType.ASSET,
        category=Category.CASH,
        debit=0,
        credit=175000,
        entry_date=today,
        fund=special_fund,
        fiscal_period=fiscal_period,
        description="Infrastructure equipment purchase",
        tags="investing,capital"
    )
    
    # Transaction 5: Grant revenue
    tx5_id = str(uuid.uuid4())[:8]
    LedgerEntry(
        id=f"LE-{tx5_id}-1",
        transaction_id=tx5_id,
        entry_type=EntryType.ASSET,
        category=Category.CASH,
        debit=50000,
        credit=0,
        entry_date=today,
        fund=capital_fund,
        fiscal_period=fiscal_period,
        description="Capital grant received",
        tags="operating"
    )
    LedgerEntry(
        id=f"LE-{tx5_id}-2",
        transaction_id=tx5_id,
        entry_type=EntryType.REVENUE,
        category=Category.GRANT,
        debit=0,
        credit=50000,
        entry_date=today,
        fund=capital_fund,
        fiscal_period=fiscal_period,
        description="Capital grant received",
        tags="operating"
    )
    
    # Transaction 6: Bond proceeds (financing)
    tx6_id = str(uuid.uuid4())[:8]
    LedgerEntry(
        id=f"LE-{tx6_id}-1",
        transaction_id=tx6_id,
        entry_type=EntryType.ASSET,
        category=Category.CASH,
        debit=100000,
        credit=0,
        entry_date=today,
        fund=capital_fund,
        fiscal_period=fiscal_period,
        description="Bond proceeds received",
        tags="financing,bond"
    )
    LedgerEntry(
        id=f"LE-{tx6_id}-2",
        transaction_id=tx6_id,
        entry_type=EntryType.LIABILITY,
        category=Category.BOND,
        debit=0,
        credit=100000,
        entry_date=today,
        fund=capital_fund,
        fiscal_period=fiscal_period,
        description="Bond liability recorded",
        tags="financing,bond"
    )
    
    "seeded"
'''
        # Escape the code for shell
        escaped_code = accounting_seed_code.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        seed_cmd = ['dfx', 'canister', 'call', backend_name, '__shell__', f'("{escaped_code}")']
        if network != 'local':
            seed_cmd.extend(['--network', network])
        result = subprocess.run(seed_cmd, cwd=realm_dir, capture_output=True, text=True)
        if result.returncode == 0:
            if "already_seeded" in result.stdout:
                print(f"   ℹ️  Accounting data already exists, skipping")
            else:
                print(f"   ✅ Demo accounting data seeded successfully")
        else:
            print(f"   ⚠️  Failed to seed accounting data: {result.stderr}")

    except Exception as e:
        print(f"   ⚠️  Accounting data seeding failed: {e} (continuing anyway)")

# Start TaskManager to schedule tasks created during data import.
# Timer callbacks MUST be created in an update call context (not __shell__).
print("\n⏱️  Starting TaskManager...")
try:
    start_cmd = ['dfx', 'canister', 'call', backend_name, 'start_task_manager']
    if network != 'local':
        start_cmd.extend(['--network', network])
    result = subprocess.run(start_cmd, cwd=realm_dir, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"   ✅ TaskManager: {result.stdout.strip()}")
    else:
        print(f"   ⚠️  Failed to start TaskManager: {result.stderr}")
except Exception as e:
    print(f"   ⚠️  TaskManager start failed: {e} (continuing anyway)")

print("\n✅ Post-deployment tasks completed")
