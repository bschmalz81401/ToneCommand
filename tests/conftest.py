import os

# Tests run with a configured store range (the empty-default behavior has
# its own dedicated tests in test_store_config.py).
os.environ.setdefault("TONECOMMAND_STORE_SLOTS", "133-148")
