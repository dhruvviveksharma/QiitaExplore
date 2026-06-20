"""E2E tests for the /deepsearch command.

Requires a running barnacle backend: bash ezredbiom/start_barnacle.sh

Test 1 (AGP): single-turn deepsearch for the American Gut Project.
  Expected studies 10317, 10395, 10768 must surface via the tool backend.

Test 2 (wild mice multi-turn): turn 1 deepsearch for wild mice; turn 2 filters
  to shotgun metagenomics. Verifies context is carried across turns via chat
  history and that study 11043 survives the filter.
"""
import pytest

from parity_helpers import llm_judge, stream_chat


@pytest.mark.e2e
@pytest.mark.e2e_llm
class TestDeepSearchAGP:
    """Single-turn /deepsearch for the American Gut Project."""

    def test_finds_agp_studies(self, backend, global_chat):
        result = stream_chat(
            backend,
            global_chat["chat_id"],
            "give me studies related to the American Gut Project",
            deep_search=True,
        )

        required = {10317, 10395, 10768}
        found = result["result_study_ids"] | result["study_ids_mentioned"]
        missing = required - found
        assert not missing, (
            f"AGP deep search missing study IDs {missing}.\n"
            f"Found: {sorted(found)}\n"
            f"Text: {result['assistant_text'][:500]}"
        )

        assert llm_judge(
            "give me studies related to the American Gut Project",
            result["assistant_text"],
            "mention or recommend studies related to the American Gut Project",
        ), f"LLM judge failed.\nText: {result['assistant_text'][:500]}"


@pytest.mark.e2e
@pytest.mark.e2e_llm
class TestDeepSearchWildMiceMultiTurn:
    """Two-turn /deepsearch: wild mice then filter to shotgun metagenomics.

    Both turns share the same chat_id so the backend passes full message history
    to the LLM on turn 2, verifying cross-turn context continuity.
    """

    def test_wild_mice_then_filter_shotgun(self, backend, global_chat):
        # Turn 1: deepsearch for wild mice studies
        turn1 = stream_chat(
            backend,
            global_chat["chat_id"],
            "find me studies related to wild mice",
            deep_search=True,
        )
        assert turn1["assistant_text"], "Expected a non-empty response for turn 1"
        assert llm_judge(
            "find me studies related to wild mice",
            turn1["assistant_text"],
            "mention or recommend wild mice microbiome studies",
        ), f"Turn 1 judge failed.\nText: {turn1['assistant_text'][:500]}"

        # Turn 2: filter using prior context — no deep_search flag needed
        turn2 = stream_chat(
            backend,
            global_chat["chat_id"],
            "filter to just the ones with shotgun metagenomics",
        )
        found = turn2["result_study_ids"] | turn2["study_ids_mentioned"]
        assert 11043 in found, (
            f"Study 11043 not found after filtering wild mice to shotgun metagenomics.\n"
            f"Found: {sorted(found)}\n"
            f"Text: {turn2['assistant_text'][:500]}"
        )
        assert llm_judge(
            "filter to just the ones with shotgun metagenomics",
            turn2["assistant_text"],
            "apply a shotgun metagenomics filter to previously found wild mice studies",
        ), f"Turn 2 judge failed.\nText: {turn2['assistant_text'][:500]}"
