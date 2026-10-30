"""Coverage of the backend-fundamentals references in the sdd-backend-engineering playbook."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "orchestrate" / "sdd-orchestrator" / "templates" / ".hermes" / "skills" / "sdd-backend-engineering"

#: Concepts each fundamentals reference must name, by file relative to the playbook.
COVERAGE = {
    "references/fundamentals-audit.md": (
        "integration-test-suite.md", "never-trust-the-client.md", "race-conditions.md",
        "webhook-idempotency.md", "separation-of-concerns.md", "not verified", "read-only",
    ),
    "references/integration-test-suite.md": (
        "cross-user access", "anti-production guard", "same engine", "never change the test to pass",
    ),
    "references/never-trust-the-client.md": (
        "recomputes", "`next_public_`", "rotation", "row-level security", "idor",
    ),
    "references/race-conditions.md": (
        "unique constraint", "atomic conditional update", "for update", "simultaneous requests",
    ),
    "references/webhook-idempotency.md": (
        "raw request body", "processed_events", "reuse it on every retry", "idempotency-key", "ordering",
    ),
    "references/separation-of-concerns.md": (
        "characterization tests", "never the test", "note bugs, do not fix them",
    ),
}


def normalized(text: str) -> str:
    return " ".join(text.lower().split())


class BackendFundamentalsPlaybookTests(unittest.TestCase):
    def test_each_reference_covers_its_fundamental(self) -> None:
        for relative, concepts in COVERAGE.items():
            path = SKILL / relative
            with self.subTest(file=relative):
                self.assertTrue(path.is_file())
                text = normalized(path.read_text(encoding="utf-8"))
                for concept in concepts:
                    with self.subTest(concept=concept):
                        self.assertIn(normalized(concept), text)

    def test_skill_routes_every_fundamentals_reference(self) -> None:
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        routing = text.split("## Reference Routing", 1)[1].split("\n## ", 1)[0]
        for relative in COVERAGE:
            with self.subTest(reference=relative):
                self.assertIn(f"`{relative}`", routing)


if __name__ == "__main__":
    unittest.main()
