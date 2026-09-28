"""Tests for migrate_to_vault: moving knowledge and runtime into the vault."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import migrate_to_vault  # noqa: E402
import obsidian_binding  # noqa: E402

VALID_STATE = """# SDD Orchestration State

```yaml
schema_version: 2
ticket:
  id: APP-100
stage:
  current: DONE
  status: SUCCESS
```
"""

OPEN_STATE = """# SDD Orchestration State

```yaml
schema_version: 2
ticket:
  id: APP-470
stage:
  current: IMPLEMENT
  status: WAITING
loop:
  control:
    loop_active: true
```
"""

CORRUPT_STATE = """# SDD Orchestration State

```yaml
schema_version: 2
stage: {current: DONE, status: COMPLETED}
evidence:
  tdd_slices:
    - {id: A, ok: 1}
    - {id: B, ok: 0}]
```
"""


class MigrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)

        self.repo = root / "repo"
        self.orchestration = self.repo / ".hermes" / "orchestration"
        self.orchestration.mkdir(parents=True)

        self.vault = root / "vault"
        self.container = self.vault / "Projects" / "Demo"
        self.container.mkdir(parents=True)

        binding_file = self.repo / ".hermes" / "obsidian.json"
        binding_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "vault_path": str(self.vault),
                    "project_container": "Projects/Demo",
                    "runtime_subpath": ".hermes-runtime",
                }
            ),
            encoding="utf-8",
        )
        self.binding = obsidian_binding.load(self.repo)

    def seed_state(self, text: str = VALID_STATE) -> Path:
        path = self.orchestration / "STATE.md"
        path.write_text(text, encoding="utf-8")
        return path

    def seed_task(self, slug: str, filename: str = "_prd.md", body: str = "prd") -> Path:
        path = self.repo / "tasks" / f"prd-{slug}" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def plan(self, **kwargs):
        return migrate_to_vault.build_plan(self.repo, self.binding, **kwargs)


class DryRunTests(MigrationTestCase):
    def test_dry_run_writes_nothing(self) -> None:
        self.seed_state()
        self.seed_task("alpha")
        before = sorted(p.name for p in self.container.rglob("*"))
        plan = self.plan()
        self.assertTrue(plan.moves)
        self.assertEqual(before, sorted(p.name for p in self.container.rglob("*")))
        self.assertTrue((self.orchestration / "STATE.md").exists())

    def test_plan_reports_source_and_destination(self) -> None:
        self.seed_state()
        plan = self.plan()
        state_move = next(m for m in plan.moves if m.source.name == "STATE.md")
        self.assertTrue(
            obsidian_binding.is_inside_container(self.binding, state_move.destination)
        )

    def test_pycache_is_discarded_not_migrated(self) -> None:
        cache = self.orchestration / "__pycache__"
        cache.mkdir()
        (cache / "x.pyc").write_bytes(b"\x00")
        plan = self.plan()
        self.assertNotIn(
            "__pycache__", {part for m in plan.moves for part in m.source.parts}
        )
        self.assertIn(cache, plan.discards)

    def test_nested_pycache_is_discarded(self) -> None:
        """Observed for real: investigations/<x>/__pycache__ left a stray .pyc."""
        nested = self.orchestration / "investigations" / "probe" / "__pycache__"
        nested.mkdir(parents=True)
        (nested / "controller.cpython-311.pyc").write_bytes(b"\x00")
        plan = self.plan()
        self.assertIn(nested, plan.discards)
        self.assertNotIn(
            "__pycache__", {part for m in plan.moves for part in m.source.parts}
        )

    def test_contracts_and_scripts_stay_in_the_repository(self) -> None:
        (self.orchestration / "LOOP_POLICY.md").write_text("policy", encoding="utf-8")
        (self.orchestration / "action_journal.py").write_text("code", encoding="utf-8")
        plan = self.plan()
        moved = {m.source.name for m in plan.moves}
        self.assertNotIn("LOOP_POLICY.md", moved)
        self.assertNotIn("action_journal.py", moved)


class SafetyTests(MigrationTestCase):
    def test_open_demand_is_refused(self) -> None:
        """app-470 is mid-slice with loop_active: migrating it moves live state."""
        self.seed_state(OPEN_STATE)
        with self.assertRaises(migrate_to_vault.MigrationRefused) as ctx:
            self.plan()
        self.assertEqual("OPEN_DEMAND", ctx.exception.code)

    def test_open_demand_can_be_skipped_explicitly(self) -> None:
        self.seed_state(OPEN_STATE)
        plan = self.plan(skip_open_demand=True)
        self.assertTrue(plan.skipped)
        self.assertEqual([], [m for m in plan.moves if m.source.name == "STATE.md"])

    def test_corrupt_state_is_reported_not_rewritten(self) -> None:
        self.seed_state(CORRUPT_STATE)
        plan = self.plan(skip_open_demand=True)
        self.assertTrue(plan.problems)
        self.assertNotIn("STATE.md", {m.source.name for m in plan.moves})

    def test_destination_collision_is_reported_never_overwritten(self) -> None:
        self.seed_task("alpha", body="from repo")
        existing = self.container / "alpha" / "_prd.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("from vault", encoding="utf-8")
        plan = self.plan()
        migrate_to_vault.apply(plan)
        self.assertEqual("from vault", existing.read_text(encoding="utf-8"))
        self.assertTrue(
            (self.container / "alpha" / "_prd.from-repo.md").exists(),
            "conflicting content must be preserved beside the original",
        )

    def test_identical_content_is_not_duplicated(self) -> None:
        self.seed_task("alpha", body="same")
        existing = self.container / "alpha" / "_prd.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("same", encoding="utf-8")
        migrate_to_vault.apply(self.plan())
        self.assertFalse((self.container / "alpha" / "_prd.from-repo.md").exists())

    def test_writes_outside_the_container_are_impossible(self) -> None:
        self.seed_state()
        plan = self.plan()
        for move in plan.moves:
            self.assertTrue(
                obsidian_binding.is_inside_container(self.binding, move.destination),
                f"{move.destination} escapes the container",
            )


class ApplyTests(MigrationTestCase):
    def test_state_is_normalised_to_json_on_arrival(self) -> None:
        self.seed_state()
        migrate_to_vault.apply(self.plan())
        runtime = obsidian_binding.runtime_dir(self.binding, self.repo)
        text = (runtime / "STATE.md").read_text(encoding="utf-8")
        payload = text.split("```yaml", 1)[1].split("```", 1)[0]
        self.assertEqual("APP-100", json.loads(payload)["ticket"]["id"])

    def test_source_is_removed_only_after_hash_verification(self) -> None:
        self.seed_task("alpha", body="payload")
        source = self.repo / "tasks" / "prd-alpha" / "_prd.md"
        migrate_to_vault.apply(self.plan())
        self.assertFalse(source.exists())
        self.assertEqual(
            "payload", (self.container / "alpha" / "_prd.md").read_text(encoding="utf-8")
        )

    def test_apply_is_idempotent(self) -> None:
        self.seed_state()
        self.seed_task("alpha")
        migrate_to_vault.apply(self.plan())
        second = self.plan()
        self.assertEqual([], second.moves)

    def test_report_counts_match_the_work_done(self) -> None:
        self.seed_state()
        self.seed_task("alpha")
        self.seed_task("beta")
        plan = self.plan()
        result = migrate_to_vault.apply(plan)
        self.assertEqual(len(plan.moves), len(result.moved))


if __name__ == "__main__":
    unittest.main()
