"""
Word Counter Codex - Downloads file from URL and counts words.

This codex demonstrates:
1. HTTP outcalls from ICP canisters
2. TaskEntity for persistent state across scheduled runs
3. Async task pattern with yield

Usage:
    basilisk-toolkit exec -f examples/file_download/word_counter.py
"""

from _cdk import ic, Async, CallResult, match
from _cdk import management_canister, HttpResponse
from ic_python_db import String, Integer
import json

# Configuration - change this URL to your target
URL = "https://raw.githubusercontent.com/smart-social-contracts/realms-gos/main/README.md"
MAX_RESPONSE_BYTES = 100_000  # 100KB limit


# State persistence using TaskEntity (automatically available in scheduled tasks)
class WordCountState(TaskEntity):
    """Persists word count results between task executions."""
    __alias__ = "key"
    key = String(max_length=256)
    value = String(max_length=5000)


def async_task() -> Async[str]:
    """
    Main entry point for the scheduled task.
    Downloads a file from URL and counts words.
    """
    ic.print(f"📥 Starting word count task")
    ic.print(f"📍 Target URL: {URL}")
    
    # Make HTTP outcall (async - requires yield)
    ic.print("🌐 Making HTTP request...")
    http_result: CallResult[HttpResponse] = yield management_canister.http_request(
        {
            "url": URL,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "method": {"get": None},
            "headers": [
                {"name": "User-Agent", "value": "Realms-WordCounter/1.0"}
            ],
            "body": None,
            "transform": None,
        }
    ).with_cycles(100_000_000)  # 100M cycles for HTTP outcall
    
    def handle_response(response: HttpResponse) -> str:
        """Process successful HTTP response."""
        try:
            # Decode response body
            content = response["body"].decode("utf-8")
            content_length = len(content)
            
            # Count words
            words = content.split()
            word_count = len(words)
            
            # Count lines
            line_count = len(content.splitlines())
            
            ic.print(f"✅ Download successful!")
            ic.print(f"📊 Content length: {content_length} bytes")
            ic.print(f"📊 Word count: {word_count}")
            ic.print(f"📊 Line count: {line_count}")
            
            # Persist result using TaskEntity
            state = WordCountState["last_result"]
            if not state:
                state = WordCountState(key="last_result", value="{}")
            
            result_data = {
                "url": URL,
                "word_count": word_count,
                "line_count": line_count,
                "content_length": content_length,
                "timestamp": int(ic.time()),
                "success": True
            }
            state.value = json.dumps(result_data)
            
            # Also track history
            history = WordCountState["run_count"]
            if not history:
                history = WordCountState(key="run_count", value="0")
            run_count = int(history.value) + 1
            history.value = str(run_count)
            
            ic.print(f"📈 Total runs: {run_count}")
            
            return json.dumps(result_data)
            
        except UnicodeDecodeError as e:
            error_msg = f"Failed to decode response as UTF-8: {str(e)}"
            ic.print(f"❌ {error_msg}")
            return json.dumps({"success": False, "error": error_msg})
        except Exception as e:
            error_msg = f"Error processing response: {str(e)}"
            ic.print(f"❌ {error_msg}")
            return json.dumps({"success": False, "error": str(e)})
    
    def handle_error(err: str) -> str:
        """Handle HTTP request failure."""
        error_msg = f"HTTP request failed: {err}"
        ic.print(f"❌ {error_msg}")
        return json.dumps({"success": False, "error": error_msg})
    
    return match(http_result, {"Ok": handle_response, "Err": handle_error})
