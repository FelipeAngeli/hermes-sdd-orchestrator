"""Tests for state_format: parsing the three STATE.md dialects in the wild.

The repository accumulated three ways of writing the STATE payload inside the
```yaml fence:

* ``json``        — a JSON object (the main worktree)
* ``yaml-block``  — indented block mappings
* ``yaml-inline`` — block mappings mixed with inline ``{...}`` / ``[...]``

Migration must read all three and must never silently mangle one.
"""

from __future__ import annotations

import json
import unittest
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import state_format  # noqa: E402

JSON_STATE = """# SDD Orchestration State

```yaml
{
  "schema_version": 2,
  "ticket": {"id": "APP-ROUTE-CACHE", "title": "Cache"},
  "stage": {"current": "DONE", "status": "SUCCESS", "completed": ["SPECIFY"]}
}
```
"""

BLOCK_STATE = """# SDD Orchestration State

O bloco YAML é a fonte de verdade.

```yaml
schema_version: 2
workspace:
  path: "/tmp/wt/app-456"
  git_common_dir: "/tmp/repo/.git"
ticket:
  id: APP-456
  title: "Corrigir bug no cartão"
  scope_confirmed:
    - "Primeiro item."
    - "Segundo item."
stage:
  current: DONE
  status: SUCCESS
  completed: []
  skipped: []
bounded_run_plan: null # No preview is inherited.
loop:
  mode: MANUAL
  control:
    loop_active: false
    human_approval_required: false
```

## Execution Log

- something happened
"""

INLINE_STATE = """# SDD Orchestration State
```yaml
schema_version: 2
repository: {name: "app-464", branch: "app/464", head: "5c1c2e64"}
ticket:
  id: APP-464
  previous_rounds: [{round: 1, result: DONE}, {round: 2, result: DONE}]
stage: {current: DONE, status: COMPLETED, completed: [SPECIFY, CLARIFY], skipped: []}
budgets:
  executor_calls: {max: 24, used: 12}
```
"""


class DetectionTests(unittest.TestCase):
    def test_detects_json_dialect(self) -> None:
        self.assertEqual("json", state_format.detect(JSON_STATE))

    def test_detects_block_dialect(self) -> None:
        self.assertEqual("yaml-block", state_format.detect(BLOCK_STATE))

    def test_detects_inline_dialect(self) -> None:
        self.assertEqual("yaml-inline", state_format.detect(INLINE_STATE))

    def test_missing_fence_is_reported(self) -> None:
        with self.assertRaises(state_format.StateFormatError) as ctx:
            state_format.parse("# no fence here\n")
        self.assertEqual("STATE_NO_YAML_FENCE", ctx.exception.code)


class ParseTests(unittest.TestCase):
    def test_parses_json_state(self) -> None:
        data = state_format.parse(JSON_STATE)
        self.assertEqual(2, data["schema_version"])
        self.assertEqual("DONE", data["stage"]["current"])

    def test_parses_nested_block_mappings(self) -> None:
        data = state_format.parse(BLOCK_STATE)
        self.assertEqual("/tmp/wt/app-456", data["workspace"]["path"])
        self.assertFalse(data["loop"]["control"]["loop_active"])

    def test_parses_block_sequences(self) -> None:
        data = state_format.parse(BLOCK_STATE)
        self.assertEqual(
            ["Primeiro item.", "Segundo item."], data["ticket"]["scope_confirmed"]
        )

    def test_parses_empty_inline_collections(self) -> None:
        data = state_format.parse(BLOCK_STATE)
        self.assertEqual([], data["stage"]["completed"])
        self.assertEqual([], data["stage"]["skipped"])

    def test_null_and_trailing_comment(self) -> None:
        data = state_format.parse(BLOCK_STATE)
        self.assertIsNone(data["bounded_run_plan"])

    def test_parses_inline_mappings(self) -> None:
        data = state_format.parse(INLINE_STATE)
        self.assertEqual("app-464", data["repository"]["name"])
        self.assertEqual(24, data["budgets"]["executor_calls"]["max"])

    def test_parses_inline_list_of_mappings(self) -> None:
        data = state_format.parse(INLINE_STATE)
        self.assertEqual(
            [{"round": 1, "result": "DONE"}, {"round": 2, "result": "DONE"}],
            data["ticket"]["previous_rounds"],
        )

    def test_parses_inline_scalar_list(self) -> None:
        data = state_format.parse(INLINE_STATE)
        self.assertEqual(["SPECIFY", "CLARIFY"], data["stage"]["completed"])

    def test_hash_inside_quotes_is_not_a_comment(self) -> None:
        text = '```yaml\ntitle: "issue #42 tracked"\n```\n'
        self.assertEqual("issue #42 tracked", state_format.parse(text)["title"])

    def test_colon_inside_quoted_value_is_preserved(self) -> None:
        text = '```yaml\npath: "C:/x: y"\n```\n'
        self.assertEqual("C:/x: y", state_format.parse(text)["path"])


class NormaliseTests(unittest.TestCase):
    def test_normalised_output_is_json_in_a_yaml_fence(self) -> None:
        out = state_format.normalise(BLOCK_STATE)
        self.assertEqual("json", state_format.detect(out))
        self.assertEqual(state_format.parse(BLOCK_STATE), state_format.parse(out))

    def test_normalisation_is_idempotent(self) -> None:
        once = state_format.normalise(INLINE_STATE)
        twice = state_format.normalise(once)
        self.assertEqual(once, twice)

    def test_prose_outside_the_fence_is_preserved(self) -> None:
        out = state_format.normalise(BLOCK_STATE)
        self.assertIn("## Execution Log", out)
        self.assertIn("- something happened", out)
        self.assertIn("O bloco YAML é a fonte de verdade.", out)

    def test_already_json_state_is_left_semantically_identical(self) -> None:
        out = state_format.normalise(JSON_STATE)
        self.assertEqual(state_format.parse(JSON_STATE), state_format.parse(out))

    def test_round_trip_verification_rejects_lossy_parse(self) -> None:
        """Normalisation must refuse rather than write a state it cannot re-read."""
        broken = "```yaml\n\tkey: value\n```\n"
        with self.assertRaises(state_format.StateFormatError):
            state_format.normalise(broken)

    def test_required_keys_are_enforced_before_normalising(self) -> None:
        with self.assertRaises(state_format.StateFormatError) as ctx:
            state_format.normalise("```yaml\nunrelated: 1\n```\n")
        self.assertEqual("STATE_SHAPE_UNRECOGNISED", ctx.exception.code)

    def test_stray_bracket_is_refused_not_guessed(self) -> None:
        """Observed in app-464: a real file carries an unmatched ']'.

        That is invalid YAML, not a dialect this parser lacks. Repairing it by
        guessing would silently rewrite orchestration history, so the only safe
        behaviour is to refuse and surface the file for human repair.
        """
        corrupt = (
            "```yaml\n"
            "schema_version: 2\n"
            "stage: {current: DONE, status: COMPLETED}\n"
            "evidence:\n"
            "  tdd_slices:\n"
            "    - {id: A, ok: 1}\n"
            "    - {id: B, ok: 0}]\n"
            "```\n"
        )
        with self.assertRaises(state_format.StateFormatError):
            state_format.normalise(corrupt)


class RealWorldShapeTests(unittest.TestCase):
    """Guards against the exact payloads observed in this repository."""

    def test_stage_and_status_survive_every_dialect(self) -> None:
        for text in (JSON_STATE, BLOCK_STATE, INLINE_STATE):
            data = state_format.parse(text)
            self.assertIn(data["stage"]["current"], {"DONE", "IDLE", "IMPLEMENT"})
            self.assertTrue(data["stage"]["status"])

    def test_normalised_json_is_machine_readable(self) -> None:
        for text in (BLOCK_STATE, INLINE_STATE):
            out = state_format.normalise(text)
            fence = out.split("```yaml", 1)[1].split("```", 1)[0]
            json.loads(fence)


if __name__ == "__main__":
    unittest.main()
