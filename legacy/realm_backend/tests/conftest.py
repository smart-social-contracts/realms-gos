"""Make ``realm_backend`` importable (``core``, ``ggg_sdk``) when running the
backend unit tests directly with pytest."""

import os
import sys

_REALM_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REALM_BACKEND not in sys.path:
    sys.path.insert(0, _REALM_BACKEND)
