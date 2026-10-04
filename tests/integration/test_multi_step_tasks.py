#!/usr/bin/env python3
"""Integration test for download_file function with real file download and checksum verification."""

import json
import subprocess
import sys
import time


def test_download_file_with_checksum_verification():
    """
    Integration test: Download a real file from GitHub and verify its checksum.

    This test:
    1. Calls the download_file canister method via dfx
    2. Downloads: https://raw.githubusercontent.com/smart-social-contracts/realms-gos/dd22e5523f84acf72ad875c34aaef52ef644d646/canister_ids.json
    3. Verifies SHA256 checksum: 1585029aba1ad0fd473a523c46664323bf68cdf0b65c082ae4130fe9dc772964
    4. Checks that the file is saved to the specified Codex
    """

    print("=" * 80)
    print("Testing download_file with checksum verification")
    print("=" * 80)

    # Test parameters
    url = "https://raw.githubusercontent.com/smart-social-contracts/realms-gos/dd22e5523f84acf72ad875c34aaef52ef644d646/canister_ids.json"
    checksum = "sha256:1585029aba1ad0fd473a523c46664323bf68cdf0b65c082ae4130fe9dc772964"
    codex_name = "test_downloaded_canister_ids"

    print(f"\n📥 Downloading: {url}")
    print(f"📋 Expected checksum: {checksum}")
    print(f"💾 Target Codex: {codex_name}")

    try:
        # Call download_file via dfx
        print("\n🔧 Calling download_file via dfx...")
        cmd = [
            "dfx",
            "canister",
            "call",
            "realm_backend",
            "download_file",
            f'("{url}", "{codex_name}", null, opt "{checksum}")',
        ]
        print("cmd: ", " ".join(cmd))

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=True
        )

        # Parse the response
        output = result.stdout.strip()
        print(f"\n📤 Raw response: {output}")

        # Extract JSON from Candid format: ("json_string")
        if output.startswith("(") and output.endswith(")"):
            inner = output[1:-1].strip()
            if inner.endswith(","):
                inner = inner[:-1].strip()
            if inner.startswith('"') and inner.endswith('"'):
                json_str = inner[1:-1]
                # Unescape the JSON
                json_str = (
                    json_str.replace("\\n", "\n")
                    .replace('\\"', '"')
                    .replace("\\\\", "\\")
                )
                data = json.loads(json_str)

                print(f"\n✅ Parsed response:")
                print(json.dumps(data, indent=2))

                # Verify the response
                assert (
                    data["success"] is True
                ), f"Download failed: {data.get('error', 'Unknown error')}"
                assert data["url"] == url, f"URL mismatch: {data['url']} != {url}"
                assert (
                    data["codex_name"] == codex_name
                ), f"Codex name mismatch: {data['codex_name']} != {codex_name}"
                assert data["checksum"] == checksum, f"Checksum not in response"
                assert "task_id" in data, "task_id not in response"

                print(f"\n✅ Download task created successfully!")
                print(f"   Task ID: {data['task_id']}")
                print(f"   Task Name: {data['task_name']}")

                # Poll for task completion
                print(
                    f"\n⏳ Polling for task completion (checking every 5s for up to 60s)..."
                )
                max_wait_time = 60  # seconds
                poll_interval = 5  # seconds
                elapsed_time = 0
                codex_found = False

                verify_cmd = [
                    "dfx",
                    "canister",
                    "call",
                    "--output",
                    "json",
                    "realm_backend",
                    "get_objects",
                    f"""(vec {{ record {{ 0 = "Codex"; 1 = "{codex_name}" }}; }})""",
                ]

                while elapsed_time < max_wait_time:
                    time.sleep(poll_interval)
                    elapsed_time += poll_interval

                    print(f"   📍 Checking at {elapsed_time}s...")

                    try:
                        verify_result = subprocess.run(
                            verify_cmd,
                            capture_output=True,
                            text=True,
                            timeout=30,
                            check=True,
                        )

                        # Parse JSON response
                        response = json.loads(verify_result.stdout)

                        if response.get("success") is True:
                            # Codex found - extract data
                            objects = response["data"]["objectsList"]["objects"]
                            if objects:
                                codex_data = json.loads(objects[0])
                                print(f"\n📋 Verification output:")
                                print(f"   ✅ Codex found: {codex_data['name']}")
                                print(
                                    f"   Content length: {len(codex_data['code'])} bytes"
                                )
                                print(f"   First 100 chars: {codex_data['code'][:100]}")
                                print(
                                    f"\n🎉 TEST PASSED: File downloaded, checksum verified, and saved to Codex!"
                                )
                                print(
                                    f"   ⏱️  Task completed in ~{elapsed_time} seconds"
                                )
                                codex_found = True
                                break
                        else:
                            # Codex not found yet
                            error = response.get("data", {}).get(
                                "error", "Unknown error"
                            )
                            print(f"   ⏳ Not ready yet: {error}")
                            continue
                    except (
                        subprocess.CalledProcessError,
                        json.JSONDecodeError,
                        KeyError,
                    ) as e:
                        print(f"   ⚠️  Check failed: {str(e)[:100]}")
                        continue

                if not codex_found:
                    print(
                        f"\n❌ TEST FAILED: Codex was not created after {max_wait_time} seconds"
                    )
                    print(f"\n📋 Last verification output:")
                    print(
                        verify_result.stdout
                        if "verify_result" in locals()
                        else "No output"
                    )
                    return False

                return True

        else:
            print(f"❌ Unexpected response format: {output}")
            return False

    except subprocess.TimeoutExpired:
        print(f"\n❌ TEST FAILED: Command timed out")
        return False
    except subprocess.CalledProcessError as e:
        print(f"\n❌ TEST FAILED: dfx command failed")
        print(f"Error: {e.stderr}")
        return False
    except Exception as e:
        print(f"\n❌ TEST FAILED: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_download_file_with_checksum_verification()
    sys.exit(0 if success else 1)
