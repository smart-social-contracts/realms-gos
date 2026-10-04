"""
Example: Create a scheduled task to download and install a codex from GitHub.

This demonstrates:
- Multi-step tasks (async download + sync install)
- Scheduled task execution (runs at a future time)
- Dynamic codex download from URL
- TaskManager and TaskSchedule usage

Run with:
    basilisk-toolkit exec -f examples/scheduled_codex_install.py
"""

from ggg import Codex
from ggg.task import Task
from ggg.task_schedule import TaskSchedule
from core.task_manager import Call, TaskStep, TaskManager
from _cdk import ic

print("=" * 70)
print("📦 EXAMPLE: Scheduled Codex Download and Installation")
print("=" * 70)

# Configuration
CODEX_URL = "https://raw.githubusercontent.com/smart-social-contracts/realms-gos/refs/heads/main/src/realm_backend/codex.py"
CODEX_NAME = "Subsidy Distribution System"
CODEX_DESCRIPTION = "Automated distribution of subsidies and benefits to eligible citizens"
SCHEDULE_DELAY_SECONDS = 300  # Run 5 minutes from now

print(f"\n📋 Configuration:")
print(f"   URL: {CODEX_URL}")
print(f"   Name: {CODEX_NAME}")
print(f"   Scheduled delay: {SCHEDULE_DELAY_SECONDS} seconds ({SCHEDULE_DELAY_SECONDS // 60} minutes)")

# ============================================================================
# STEP 1: Create async download step
# ============================================================================
print("\n🔧 Step 1/2: Creating async download step...")

download_codex = Codex(
    name=f"_download_{CODEX_NAME}",
    code=f'''def async_task():
    """Async task to download codex code from GitHub."""
    from main import download_file_from_url
    from _cdk import ic
    
    url = "{CODEX_URL}"
    ic.print(f"📥 Starting download from {{url}}...")
    
    try:
        # Download the file (async operation using IC HTTP outcalls)
        result = yield download_file_from_url(url)
        
        ic.print(f"✅ Download complete! Size: {{len(result)}} bytes")
        
        # Preview first 100 chars
        preview = result[:100].replace("\\n", " ")
        ic.print(f"Preview: {{preview}}...")
        
        return result
    except Exception as e:
        ic.print(f"❌ Download failed: {{str(e)}}")
        raise
'''
)

download_call = Call(is_async=True, codex=download_codex)
download_step = TaskStep(call=download_call, run_next_after=0)

print("✅ Async download step created")

# ============================================================================
# STEP 2: Create sync install step
# ============================================================================
print("🔧 Step 2/2: Creating sync install step...")

install_codex = Codex(
    name=f"_install_{CODEX_NAME}",
    code=f'''from ggg import Codex
from _cdk import ic

ic.print("📦 Installing codex from downloaded content...")

# In a production implementation, you would:
# 1. Retrieve the downloaded content from the previous step
# 2. Set it as the codex.code
# For this example, we create a placeholder codex

codex = Codex(
    name="{CODEX_NAME}",
    description="{CODEX_DESCRIPTION}"
)

# Note: In a real implementation, you'd need to pass the downloaded
# content between steps. This could be done via:
# - Storing in a temporary entity
# - Using the task context
# - Shared state mechanism

# Placeholder: codex.code = downloaded_content

ic.print(f"✅ Codex created:")
ic.print(f"   ID: {{codex._id}}")
ic.print(f"   Name: {{codex.name}}")
ic.print(f"   Description: {{codex.description}}")

result = {{
    "codex_id": codex._id,
    "name": codex.name,
    "description": codex.description
}}
'''
)

install_call = Call(is_async=False, codex=install_codex)
install_step = TaskStep(call=install_call, run_next_after=2)  # Wait 2 seconds after download

print("✅ Sync install step created")

# ============================================================================
# STEP 3: Create multi-step task
# ============================================================================
print("\n🔗 Creating multi-step task...")

task = Task(
    name=f"Install {CODEX_NAME}",
    metadata=f'{{"description": "Download and install codex from GitHub", "url": "{CODEX_URL}"}}',
    steps=[download_step, install_step]
)

print(f"✅ Task created with {len(list(task.steps))} steps")

# ============================================================================
# STEP 4: Schedule the task for future execution
# ============================================================================
print("\n⏰ Creating schedule...")

current_time = int(ic.time() / 1_000_000_000)  # Convert nanoseconds to seconds
run_at_time = current_time + SCHEDULE_DELAY_SECONDS

schedule = TaskSchedule(
    name=f"schedule_{CODEX_NAME}",
    task=task,
    run_at=run_at_time,
    repeat_every=0,  # Run once only (set to N seconds for recurring)
    last_run_at=0,
    disabled=False
)

print(f"✅ Schedule created:")
print(f"   Current time: {current_time} (unix timestamp)")
print(f"   Scheduled for: {run_at_time} (unix timestamp)")
print(f"   Delay: {SCHEDULE_DELAY_SECONDS} seconds")

# ============================================================================
# STEP 5: Register with TaskManager
# ============================================================================
print("\n🚀 Registering task with TaskManager...")

# Note: In a production system, the TaskManager would be running
# continuously and checking schedules via _update_timers()
# For this example, we just register the task
manager = TaskManager()
manager.add_task(task)

print("✅ Task registered successfully!")

# ============================================================================
# SUMMARY
# ============================================================================
print("\n" + "=" * 70)
print("✅ SCHEDULED TASK CREATED SUCCESSFULLY!")
print("=" * 70)
print(f"\n📊 Task Details:")
print(f"   Task ID: {task._id}")
print(f"   Schedule ID: {schedule._id}")
print(f"   Steps: {len(list(task.steps))}")
print(f"   Scheduled time: {run_at_time}")
print(f"   Status: {task.status}")

print(f"\n💡 Next Steps:")
print(f"   1. The TaskManager will automatically execute this task at the scheduled time")
print(f"   2. Monitor execution in the canister logs")
print(f"   3. Check task status with:")
print(f"      dfx canister call realm_backend get_task_status '(\"{task._id}\")'")

print(f"\n📝 What happens when the task executes:")
print(f"   1. Step 1 (Async): Downloads codex from {CODEX_URL}")
print(f"   2. Wait 2 seconds")
print(f"   3. Step 2 (Sync): Installs the downloaded code as a Codex entity")

print(f"\n⚙️ To make this recurring, set repeat_every to a non-zero value (in seconds)")
print("=" * 70)

# Return result for shell output
result = {
    "task_id": task._id,
    "schedule_id": schedule._id,
    "scheduled_time": run_at_time,
    "delay_seconds": SCHEDULE_DELAY_SECONDS,
    "steps": len(list(task.steps))
}
