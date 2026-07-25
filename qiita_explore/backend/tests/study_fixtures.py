"""Shared real-Qiita-ID fixtures for e2e tests.

Previously redeclared per-file (16084 in test_blocked_studies.py and
test_chat_search_consistency.py; 11043 inline in several files). Centralized
here so the pi-backend test matrix (tests/e2e/test_internal_tools.py,
tests/e2e/test_project_scope.py) doesn't add yet more copies — extend this
file, not a new local constant, as more known-good/known-bad IDs are needed.
"""

# Canonical known-public study with samples — the most reused fixture value
# in the suite (test_pinned_studies.py, test_pin_command.py,
# test_search_parity.py, test_chat_search_consistency.py, test_deepsearch.py).
PUBLIC_STUDY_WITH_SAMPLES = 11043

# Canonical known-private/blocked study (test_blocked_studies.py,
# test_chat_search_consistency.py, test_pinned_studies.py).
PRIVATE_STUDY = 16084

# American Gut Project studies — large, public, always available
# (test_deepsearch.py).
AGP_STUDIES = {10317, 10395, 10768}

# Guaranteed-nonexistent sentinel (test_pin_command.py e2e uses 999999; unit
# tier uses 99999 for a different purpose — keep this one for e2e).
NONEXISTENT_STUDY = 999999
