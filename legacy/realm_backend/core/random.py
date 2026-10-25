"""
ICP-compatible unique ID generation.

uuid.uuid4() doesn't work on ICP canisters (no system entropy).
Uses ic.time() + counter instead.
"""

import hashlib

from _cdk import ic

# Ensures uniqueness even if multiple IDs are generated in the same ic.time() call
_counter = 0


def generate_unique_id(prefix: str = "", length: int = 12) -> str:
    """Generate unique ID: prefix + hash(timestamp + counter)."""
    global _counter
    _counter += 1
    data = f"{ic.time()}_{_counter}"
    return f"{prefix}{hashlib.sha256(data.encode()).hexdigest()[:length]}"
