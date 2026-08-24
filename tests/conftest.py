import os
import sys
from pathlib import Path

# CI runs `pytest tests/ -q`, and the bare pytest entry point does NOT put the
# working directory on sys.path the way `python -m pytest` does. tools/ is not
# an installed package (pyproject ships fm9 and server only), so importing a
# tool in a test fails at collection without this. Doing it in conftest also
# removes an accidental dependency on collection order: a module-scope import
# of tools used to work only if some earlier test module had already inserted
# the root as a side effect.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Tests run with a configured store range (the empty-default behavior has
# its own dedicated tests in test_store_config.py).
os.environ.setdefault("TONECOMMAND_STORE_SLOTS", "133-148")
