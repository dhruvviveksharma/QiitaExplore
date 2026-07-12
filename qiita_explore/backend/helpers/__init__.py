# Ensure the backend root (parent of this package) is on sys.path so that
# top-level modules like config, store, and sql_store_* are importable from
# any submodule, regardless of how this package was first imported.
import sys
import os

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
