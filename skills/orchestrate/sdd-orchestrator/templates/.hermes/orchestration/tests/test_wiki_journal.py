"""Behavior of the wiki journal: everything the orchestrator runs lands in the project wiki.

Every test runs the module from a real controller copy (vault-resident or
repository-local) so the trust decision is exercised exactly as installed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ORCHESTRATION = Path(__file__).resolve().parents[1]
RUNTIME = ORCHESTRATION / "runtime"
sys.path.insert(0, str(RUNTIME))

import wiki_layout  # noqa: E402

WHEN = dt.datetime(2026, 10, 7, 12, 30, 5, tzinfo=dt.timezone.utc)
CONTROLLER_MODULES = ("obsidian_binding", "wiki_layout", "wiki_journal", "action_journal")


def load_controller(root: Path) -> dict:
    """Copy the controller under ``root`` and import its modules from there."""
    orchestration = root / ".hermes" / "orchestration"
    ignore = shutil.ignore_patterns("__pycache__")
    shutil.copytree(RUNTIME, orchestration / "runtime", ignore=ignore, dirs_exist_ok=True)
    shutil.copytree(ORCHESTRATION / "hooks", orchestration / "hooks", ignore=ignore, dirs_exist_ok=True)
    runtime = orchestration / "runtime"
    saved = {key: sys.modules.get(key) for key in CONTROLLER_MODULES}
    modules: dict = {}
    try:
        for key in CONTROLLER_MODULES:
            spec = importlib.util.spec_from_file_location(key, runtime / f"{key}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[key] = module
            spec.loader.exec_module(module)
            modules[key] = module
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
    return modules


def fake(prefix: str, body: str) -> str:
    """Build a credential-shaped test value without a literal secret in the source."""
    return prefix + body


class JournalTestCase(unittest.TestCase):
    """A vault-resident controller in ``Projects/App`` serving the registered worktree ``code/repo``."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd wiki journal-")
        self.addCleanup(temp.cleanup)
        self.base = Path(os.path.realpath(temp.name))
        self.vault = self.base / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.container = self.vault / "Projects" / "App"
        self.container.mkdir(parents=True)
        wiki_layout.init(self.container, project="App", today="2026-10-07")
        self.repo = self.base / "code" / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.write_binding(self.container)
        self.register(self.repo)
        modules = load_controller(self.container)
        self.wj = modules["wiki_journal"]
        self.aj = modules["action_journal"]
        # action_journal imports wiki_journal lazily: make it find this controller's copy.
        patcher = mock.patch.dict(sys.modules, {"wiki_journal": self.wj})
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("TERMINAL_CWD", None)

    def write_binding(self, root: Path, container: str = "Projects/App") -> None:
        binding = root / ".hermes" / "obsidian.json"
        binding.parent.mkdir(parents=True, exist_ok=True)
        binding.write_text(json.dumps({"schema_version": 1, "vault_path": str(self.vault), "project_container": container}), encoding="utf-8")

    def register(self, workspace: Path, slug: str = "repo-1") -> None:
        runtime = self.container / ".hermes-runtime" / slug
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "STATE.md").write_text(f'---\nworkspace:\n  path: "{workspace}"\n---\n', encoding="utf-8")

    def record(self, **kwargs) -> dict:
        kwargs.setdefault("when", WHEN)
        return self.wj.record(self.container, **kwargs)

    def log(self) -> str:
        return (self.container / "log.md").read_text(encoding="utf-8")

    @staticmethod
    def files(root: Path) -> list[str]:
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


class RecordTests(JournalTestCase):
    def test_stage_artifact_lands_in_raw_articles_of_the_ticket_and_is_logged(self) -> None:
        result = self.record(kind="stage", title="Login spec", body="# Spec\n\nUsers sign in.", ticket="APP-12", stage="SPECIFY")
        self.assertEqual("WRITTEN", result["status"])
        self.assertEqual("raw/articles/app-12/20261007-123005-specify-login-spec.md", result["path"])
        text = (self.container / result["path"]).read_text(encoding="utf-8")
        self.assertIn('kind: "stage"', text)
        self.assertIn('ticket: "APP-12"', text)
        self.assertIn("Users sign in.", text)
        self.assertIn("ingest | stage — Login spec", self.log())
        self.assertIn("[[raw/articles/app-12/20261007-123005-specify-login-spec]]", self.log())

    def test_raw_records_are_never_overwritten(self) -> None:
        first = self.record(kind="gate", title="unit tests", body="PASS", ticket="APP-12")
        second = self.record(kind="gate", title="unit tests", body="FAIL", ticket="APP-12")
        self.assertNotEqual(first["path"], second["path"])
        self.assertTrue(second["path"].endswith("-unit-tests-2.md"))
        self.assertIn("PASS", (self.container / first["path"]).read_text(encoding="utf-8"))
        self.assertIn("FAIL", (self.container / second["path"]).read_text(encoding="utf-8"))

    def test_record_without_ticket_goes_to_no_ticket(self) -> None:
        self.assertTrue(self.record(kind="action", title="run", body="done")["path"].startswith("raw/articles/no-ticket/actions/"))

    def test_decision_creates_a_concept_page_indexes_it_and_appends_later_records(self) -> None:
        first = self.record(kind="decision", title="Use JWT refresh", body="Short-lived access tokens.", ticket="APP-12", stage="PLAN")
        self.assertEqual("concepts/use-jwt-refresh.md", first["path"])
        self.assertTrue(first["created"])
        index = (self.container / "index.md").read_text(encoding="utf-8")
        self.assertIn("[[concepts/use-jwt-refresh|use-jwt-refresh]] — Short-lived access tokens.", index)
        self.assertIn("Total pages: 1", index)
        second = self.record(kind="decision", title="Use JWT refresh", body="Rotation every 15 min.", ticket="APP-12", stage="REVIEW")
        self.assertFalse(second["created"])
        page = (self.container / first["path"]).read_text(encoding="utf-8")
        self.assertIn('type: "decision"', page)
        self.assertIn("Rotation every 15 min.", page)
        self.assertEqual(1, (self.container / "index.md").read_text(encoding="utf-8").count("[[concepts/use-jwt-refresh"))
        self.assertIn("create | decision — Use JWT refresh", self.log())
        self.assertIn("update | decision — Use JWT refresh", self.log())

    def test_layer_two_kinds_go_to_their_folders(self) -> None:
        for kind, folder in (("entity", "entities"), ("concept", "concepts"), ("comparison", "comparisons"), ("query", "queries")):
            with self.subTest(kind=kind):
                self.assertTrue(self.record(kind=kind, title=f"{kind} page", body="text")["path"].startswith(folder + "/"))

    def test_turns_of_one_session_append_to_one_transcript(self) -> None:
        first = self.record(kind="turn", title="turn 1", body="hello", session="s-1")
        second = self.record(kind="turn", title="turn 2", body="world", session="s-1")
        self.assertEqual("raw/transcripts/sessions/2026-10-07-s-1.md", first["path"])
        self.assertEqual(first["path"], second["path"])
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        text = (self.container / first["path"]).read_text(encoding="utf-8")
        self.assertLess(text.index("hello"), text.index("world"))
        self.assertEqual(1, self.log().count("session s-1"))

    def test_a_full_transcript_continues_in_a_new_part(self) -> None:
        with mock.patch.object(self.wj, "MAX_TRANSCRIPT_BYTES", 2000):
            paths = {self.record(kind="turn", title=f"turn {n}", body="x" * 600, session="s-2")["path"] for n in range(6)}
        self.assertIn("raw/transcripts/sessions/2026-10-07-s-2-part2.md", paths)
        for path in paths:
            self.assertLessEqual((self.container / path).stat().st_size, 3000)

    def test_log_rotates_after_max_entries(self) -> None:
        with mock.patch.object(self.wj, "MAX_LOG_ENTRIES", 5):
            for n in range(8):
                self.record(kind="gate", title=f"g{n}", body="ok")
        self.assertTrue((self.container / "log-2026.md").is_file())
        self.assertLess(self.log().count("\n## ["), 6)

    def test_secrets_are_redacted_before_writing(self) -> None:
        samples = {
            "openai": (fake("sk-", "abcdefghijklmnopqrstuvwxyz123456"), "abcdefghijklmnopqrstuvwxyz123456"),
            "env": (fake("TYPESAFE_API_KEY=", "supersecretvalue1"), "supersecretvalue1"),
            "bearer": (fake("Authorization: Bearer ", "abcdefghijklmnopqrstuvwx"), "abcdefghijklmnopqrstuvwx"),
            "basic": (fake("Authorization: Basic ", "dXNlcjpwYXNzd29yZDEyMw=="), "dXNlcjpwYXNzd29yZDEyMw"),
            "json_key": (fake('{"api_key": "', 'jsonsecretvalue42"}'), "jsonsecretvalue42"),
            "json_pw": (fake('{"password":"', 'hunter2hunter2"}'), "hunter2hunter2"),
            "json_env": (fake('{"TYPESAFE_API_KEY": "', 'anothersecret99"}'), "anothersecret99"),
            "yaml_spaces": (fake('password: "', 'frase com espacos secreta"'), "frase com espacos secreta"),
            "url": (fake("DATABASE_URL=postgres://user:", "urlpassword9@db:5432/app"), "urlpassword9"),
            "pem": (fake("-----BEGIN OPENSSH PRIVATE", " KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----"), "b3BlbnNzaC1rZXktdjEAAAAA"),
            "jwt": (fake("eyJhbGciOiJIUzI1NiJ9.", "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"), "dozjgNryP4J3jVmNHl0w5N"),
            "gh_pat": (fake("github_pat_", "11ABCDEFG0123456789_abcdefghijklmnop"), "11ABCDEFG0123456789"),
            "google": (fake("AIza", "SyA1234567890abcdefghijklmnopqrstuv"), "SyA1234567890abcdefghij"),
            "stripe": (fake("sk_live_", "51Habcdefghijklmnopqrstuv"), "51Habcdefghijklmnop"),
            "curl": (fake("curl -u admin:", "curlsecret77 https://x"), "curlsecret77"),
            "prose": (fake("my password is ", "prosesecret55"), "prosesecret55"),
            "slack": (fake("https://hooks.slack.com/services/", "T000/B000/XXXXXXXXXXXXXXXX"), "XXXXXXXXXXXXXXXX"),
            "pgp": (fake("-----BEGIN PGP PRIVATE", " KEY BLOCK-----\nlQOYBFpgpbody\n-----END PGP PRIVATE KEY BLOCK-----"), "lQOYBFpgpbody"),
            "url_with_at": (fake("https://u:", "p@sswith@host/x"), "sswith"),
            "curl_eq": (fake("curl --user=admin:", "pwcurlequal1 x"), "pwcurlequal1"),
            "password_flag": (fake("tool --password ", "hunter9flag"), "hunter9flag"),
            "sshpass": (fake("sshpass -p ", "sshsecret7 ssh host"), "sshsecret7"),
            "mysql": (fake("mysql -u root -p", "mysqlsecret6 db"), "mysqlsecret6"),
            "glpat": (fake("glpat-", "abcdefghij1234567890"), "abcdefghij1234567890"),
            "hf": (fake("hf_", "abcdefghijklmnopqrstuv"), "abcdefghijklmnopqrstuv"),
            "node_registry": (fake("npm_", "abcdefghijklmnopqrstuv12"), "abcdefghijklmnopqrstuv12"),
            "whsec": (fake("whsec_", "abcdefghijklmnopqrstu"), "abcdefghijklmnopqrstu"),
            "azure": (fake("AccountName=a;AccountKey=", "azurekeyvalue==;EndpointSuffix=x"), "azurekeyvalue"),
            "cookie": (fake("Cookie: session=", "cookievalue5; other=1"), "cookievalue5"),
            "escaped_json": (fake('{\\"api_key\\": \\"', 'escapedsecret4\\"}'), "escapedsecret4"),
            "numeric_password": (fake("password: ", "123456"), "123456"),
            "url_empty_user": (fake("redis://:", "redispass1@host:6379"), "redispass1"),
            "url_slash": (fake("https://u:", "pa/sslash1@host/x"), "pa/sslash1"),
            "mysql_quoted": (fake("mysql -u r -p'", "mysqlquoted1' db"), "mysqlquoted1"),
            "password_quoted": (fake('tool --password "', 'a b quoted1"'), "a b quoted1"),
            "cookie_inline": (fake('curl -H "Cookie: sid=', 'cookieinline1" x'), "cookieinline1"),
        }
        result = self.record(kind="stage", title="setup", stage="PLAN", body="\n".join(sample for sample, _ in samples.values()))
        text = (self.container / result["path"]).read_text(encoding="utf-8")
        for name, (_, value) in samples.items():
            with self.subTest(secret=name):
                self.assertNotIn(value, text)
        self.assertIn("[REDACTED]", text)

    def test_redaction_stays_linear_on_adversarial_input(self) -> None:
        import time

        size = self.wj.MAX_RECORD_BYTES
        inputs = {
            "mysql": "mysql " * (size // 6),
            "urls": "http://a:b@c " * (size // 13),
            "schemes": "a://:" * (size // 5),
            "at_signs": "https://u:" + "@" * size,
            "cookies": "Cookie: " * (size // 8),
            "passwords": "--password " * (size // 11),
            "keys": '"secret":' * (size // 9),
            "words": "token " * (size // 6),
            "scheme_like_runs": ("a-" * size)[:size],
            "dotted_runs": ("a." * size)[:size],
            "plus_runs": ("a1+" * size)[:size],
            "scheme_separators": ("a://" * size)[:size],
            "scheme_with_user": ("a://x:" * size)[:size],
            "jwt_prefix_runs": ("eyJ-" * size)[:size],
            "hyphen_then_jwt": ("-eyJ" * size)[:size],
            "hyphens": "-" * size,
            "hyphen_words": ("x-eyJ-" * size)[:size],
        }
        for name, text in inputs.items():
            with self.subTest(input=name):
                started = time.monotonic()
                self.wj.redact(text)
                self.assertLess(time.monotonic() - started, 3.0)

    def test_every_redaction_pattern_is_linear_on_repeated_prefixes(self) -> None:
        """Each credential prefix repeated with each separator: quadratic patterns take seconds here."""
        import time

        size = 128 * 1024
        prefixes = (
            "eyJ", "sk-", "github_pat_", "ghp_", "glpat-", "hf_", "whsec_", "xoxb-", "AIza", "AKIA",
            "-----BEGIN RSA PRIVATE KEY-----", "a://", "https://u:", "Bearer ", "Cookie:", "AccountKey=",
            "--user=", "--password ", "sshpass -p", "mysql -p", "password is ", '"api_key":', "x_token_y=", "a",
        )
        separators = ("-", ".", "_", "+", "/", "=", ":", "@", " ", '"', "\\", "\n")
        slowest = (0.0, "")
        for prefix in prefixes:
            for separator in separators:
                unit = prefix + separator
                text = (unit * (size // len(unit) + 1))[:size]
                started = time.monotonic()
                self.wj.redact(text)
                slowest = max(slowest, (time.monotonic() - started, repr(unit)))
        self.assertLess(slowest[0], 1.5, slowest)

    def test_oversized_body_is_truncated_before_redaction(self) -> None:
        with mock.patch.object(self.wj, "redact", wraps=self.wj.redact) as spy:
            self.record(kind="action", title="big", body="mysql " * (self.wj.MAX_RECORD_BYTES // 3))
        self.assertTrue(all(len(call.args[0].encode("utf-8")) <= self.wj.MAX_RECORD_BYTES + 200 for call in spy.call_args_list))

    def test_tokens_are_redacted_after_punctuation_and_inside_urls(self) -> None:
        jwt = fake("eyJhbGciOiJIUzI1NiJ9.", "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")
        tokens = {
            "openai": (fake("sk-proj-", "Abcdefghijklmnopqrstuv12"), "Abcdefghijklmnopqrstuv12"),
            "stripe": (fake("sk_live_", "51Habcdefghijklmnopqrstuv"), "51Habcdefghijklmnop"),
            "hf": (fake("hf_", "abcdefghijklmnopqrstuv"), "abcdefghijklmnopqrstuv"),
            "github": (fake("ghp_", "abcdefghijklmnopqrstuvwxyz12"), "abcdefghijklmnopqrstuvwxyz12"),
            "gitlab": (fake("glpat-", "abcdefghij1234567890"), "abcdefghij1234567890"),
            "aws": (fake("AKIA", "ABCDEFGHIJKLMNOP"), "ABCDEFGHIJKLMNOP"),
            "google": (fake("AIza", "SyA1234567890abcdefghijklmnopqrstuv"), "SyA1234567890abcdefghij"),
            "webhook": (fake("whsec_", "abcdefghijklmnopqrstu"), "abcdefghijklmnopqrstu"),
            "jwt": (jwt, "dozjgNryP4J3jVmNHl0w5N"),
        }
        contexts = ("KEY=", "export OPENAI=", "--data=", "id=", "(", "[", '"', ",", ":", "/", "?jwt=",
                    "https://app.example.com/magic/", "https://x/api?k=", ".", "+", "a=b&t=", "\n")
        for context in contexts:
            for name, (token, secret) in tokens.items():
                with self.subTest(context=context, token=name):
                    self.assertNotIn(secret, self.wj.redact(f"{context}{token} tail"))

    def test_a_jwt_is_redacted_whole_after_a_hyphen(self) -> None:
        """A hyphen before the token must not leave the payload and signature in clear."""
        jwt = fake("eyJhbGciOiJIUzI1NiJ9.", "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")
        signature = "dozjgNryP4J3jVmNHl0w5N"
        for context in ("session-", "x-auth-", "refresh-token-", "Set-Cookie--", "jwt-", "sk-", "AIza-", "glpat-", "-", "a-b-"):
            with self.subTest(context=context):
                redacted = self.wj.redact(context + jwt)
                self.assertNotIn(signature, redacted)
                self.assertNotIn("eyJzdWIiOiIxMjM0NTY3ODkw", redacted)
        hyphen_in_header = fake("eyJhbG-iOiJIUzI1NiJ9.", "eyJzdWIiOiIxMjM0NTY3ODkwIn0.") + signature
        self.assertNotIn(signature, self.wj.redact("token " + hyphen_in_header))

    def test_url_password_after_punctuation_and_sshpass_after_brackets_are_redacted(self) -> None:
        for context in ("-", ".", "+", "(", '"', "="):
            with self.subTest(context=context):
                self.assertNotIn("urlpassword9", self.wj.redact(context + fake("postgres://user:", "urlpassword9@db/app")))
        for context in ("(", '"', ",", "=", "[", "'", ":", "/", ".", "-", "+", "<", "|", "*"):
            with self.subTest(sshpass=context):
                self.assertNotIn("sshsecret7", self.wj.redact(context + fake("sshpass -p ", "sshsecret7 ssh h")))

    def test_redaction_keeps_ordinary_values(self) -> None:
        text = self.wj.redact('max_tokens: 4096\ntoken_count = 12\n"auth": true\nTOTAL=7\nport: 5432\nhttps://example.com/a@b')
        self.assertIn("port: 5432", text)
        self.assertIn("https://example.com/a@b", text)
        self.assertEqual("see https://docs.x/y and mail me@x.com", self.wj.redact("see https://docs.x/y and mail me@x.com"))
        self.assertIn("max_tokens: 4096", text)
        self.assertIn("token_count = 12", text)
        self.assertIn('"auth": true', text)

    def test_title_cannot_forge_a_log_entry_and_metadata_is_allow_listed(self) -> None:
        self.record(kind="gate", title="ok\n## [2099-01-01] delete | everything", body="x",
                    metadata={"publish": True, "cssclasses": "evil", "outcome": "PASS\nmore"})
        self.assertNotIn("\n## [2099-01-01]", self.log())
        page = next((self.container / "raw" / "articles" / "no-ticket" / "gates").iterdir()).read_text(encoding="utf-8")
        self.assertNotIn("publish", page)
        self.assertNotIn("cssclasses", page)
        self.assertIn('outcome: "PASS more"', page)

    def test_oversized_body_is_truncated(self) -> None:
        result = self.record(kind="action", title="big", body="x" * (self.wj.MAX_RECORD_BYTES + 10))
        text = (self.container / result["path"]).read_text(encoding="utf-8")
        self.assertIn("> Truncated", text)
        self.assertLess(len(text.encode("utf-8")), self.wj.MAX_RECORD_BYTES + 2000)

    def test_unknown_kind_and_empty_title_are_rejected(self) -> None:
        with self.assertRaises(self.wj.WikiJournalError) as raised:
            self.record(kind="note", title="x", body="y")
        self.assertEqual("WIKI_RECORD_KIND_INVALID", raised.exception.code)
        with self.assertRaises(self.wj.WikiJournalError) as raised:
            self.record(kind="stage", title="  ", body="y")
        self.assertEqual("WIKI_RECORD_INVALID", raised.exception.code)

    def test_symlinked_folder_never_redirects_a_write(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (self.container / "raw" / "articles" / "app-9").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(self.wj.WikiJournalError):
            self.record(kind="stage", title="spec", body="x", ticket="APP-9", stage="SPECIFY")
        self.assertEqual([], list(outside.iterdir()))

    def test_symlinked_transcript_is_refused(self) -> None:
        outside = self.base / "outside.md"
        outside.write_text("keep\n", encoding="utf-8")
        folder = self.container / "raw" / "transcripts" / "sessions"
        folder.mkdir(parents=True)
        (folder / "2026-10-07-s-1.md").symlink_to(outside)
        with self.assertRaises(self.wj.WikiJournalError):
            self.record(kind="turn", title="t", body="x", session="s-1")
        self.assertEqual("keep\n", outside.read_text(encoding="utf-8"))

    def test_hard_linked_concept_page_is_refused(self) -> None:
        outside = self.base / "outside.md"
        outside.write_text("keep\n", encoding="utf-8")
        os.link(outside, self.container / "concepts" / "shared.md")
        with self.assertRaises(self.wj.WikiJournalError) as raised:
            self.record(kind="concept", title="shared", body="x")
        self.assertEqual("WIKI_PATH_UNSAFE", raised.exception.code)
        self.assertEqual("keep\n", outside.read_text(encoding="utf-8"))

    def test_concurrent_writers_never_lose_an_index_entry(self) -> None:
        script = self.container / ".hermes" / "orchestration" / "runtime" / "wiki_journal.py"
        code = (
            "import sys, importlib.util; spec = importlib.util.spec_from_file_location('wiki_journal', sys.argv[1]);"
            "m = importlib.util.module_from_spec(spec); sys.modules['wiki_journal'] = m; spec.loader.exec_module(m);"
            "m.record(m.Path(sys.argv[2]), kind='concept', title='page ' + sys.argv[3], body='summary')"
        )
        processes = [
            subprocess.Popen([sys.executable, "-B", "-c", code, str(script), str(self.container), str(n)])
            for n in range(12)
        ]
        self.assertEqual([0] * 12, [process.wait(60) for process in processes])
        index = (self.container / "index.md").read_text(encoding="utf-8")
        for n in range(12):
            self.assertIn(f"[[concepts/page-{n}|page-{n}]]", index)

    def test_uninitialized_container_gets_the_skeleton_before_the_first_record(self) -> None:
        fresh = self.vault / "Projects" / "Fresh"
        fresh.mkdir()
        container = self.wj.prepare_container(fresh)
        result = self.wj.record(container, kind="stage", title="spec", body="x", stage="SPECIFY", when=WHEN)
        self.assertTrue((fresh / "SCHEMA.md").is_file())
        self.assertTrue((fresh / result["path"]).is_file())

    def test_vault_root_is_never_a_container(self) -> None:
        with self.assertRaises(self.wj.WikiJournalError) as raised:
            self.wj.prepare_container(self.vault)
        self.assertEqual("WIKI_CONTAINER_IS_VAULT", raised.exception.code)


class TrustTests(JournalTestCase):
    def test_vault_controller_records_for_its_registered_worktree(self) -> None:
        result = self.wj.record_for_workspace(self.repo, kind="gate", title="lint", body="ok", when=WHEN)
        self.assertTrue((self.container / result["path"]).is_file())

    def test_unregistered_worktree_is_refused(self) -> None:
        stray = self.base / "stray"
        (stray / ".git").mkdir(parents=True)
        with self.assertRaises(self.wj.WikiJournalError) as raised:
            self.wj.record_for_workspace(stray, kind="gate", title="lint", body="ok")
        self.assertEqual("WIKI_WORKSPACE_NOT_REGISTERED", raised.exception.code)

    def test_vault_controller_ignores_a_binding_planted_in_the_repository(self) -> None:
        evil = self.vault / "attacker" / "Evil"
        evil.mkdir(parents=True)
        self.write_binding(self.repo, "attacker/Evil")
        result = self.wj.record_for_workspace(self.repo, kind="gate", title="lint", body="ok", when=WHEN)
        self.assertTrue((self.container / result["path"]).is_file())
        self.assertEqual([], self.files(evil))

    def test_unsafe_registrations_are_ignored(self) -> None:
        home = Path(os.path.realpath(os.path.expanduser("~")))
        (self.base / "plain").mkdir()
        for slug, path in (("root", Path("/")), ("home", home), ("nogit", self.base / "plain")):
            self.register(path, slug)
        self.assertEqual([self.repo], self.wj.registered_workspaces(self.container))

    def test_local_controller_records_only_for_its_own_repository(self) -> None:
        local_repo = self.base / "local"
        (local_repo / ".git").mkdir(parents=True)
        self.write_binding(local_repo)
        wj = load_controller(local_repo)["wiki_journal"]
        result = wj.record_for_workspace(local_repo, kind="gate", title="lint", body="ok", when=WHEN)
        self.assertTrue((self.container / result["path"]).is_file())
        with self.assertRaises(wj.WikiJournalError) as raised:
            wj.record_for_workspace(self.repo, kind="gate", title="lint", body="ok")
        self.assertEqual("WIKI_WORKSPACE_NOT_REGISTERED", raised.exception.code)

    def test_local_controller_records_turns_of_its_repository(self) -> None:
        local_repo = self.base / "local"
        (local_repo / ".git").mkdir(parents=True)
        self.write_binding(local_repo)
        wj = load_controller(local_repo)["wiki_journal"]
        payload = {"session_id": "s-local", "cwd": str(local_repo / "lib"), "extra": {"user_message": "hi", "assistant_response": "hello"}}
        self.assertEqual("WRITTEN", wj.record_turn_event(payload)["status"])


class ActionJournalMirrorTests(JournalTestCase):
    def journal(self, *, content: bytes | None = None, path: Path | None = None) -> dict:
        artifact = path or (self.base / "final-message.json")
        if content is None:
            content = json.dumps({"executor_result": {"summary": "plan ready"}}).encode("utf-8")
        if path is None:
            artifact.write_bytes(content)
        return {
            "journal_version": 1,
            "workspace": {"path": str(self.repo), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.repo / ".git")},
            "action": {
                "id": "APP-12-20261007T000000Z-01", "ticket": "APP-12", "stage": "PLAN", "name": "plan", "status": "RELEASED",
                "executor": "CODEX", "final_message_path": str(artifact), "attempt": 1, "invalid_fields": [],
            },
            "process": {"started_at": "2026-10-07T00:00:00Z", "finished_at": "2026-10-07T00:01:00Z", "exit_code": 0},
            "artifact": {"exists": True, "sha256": hashlib.sha256(content).hexdigest(), "validation_status": "VALID"},
        }

    def test_mirror_action_writes_the_validated_executor_result(self) -> None:
        result = self.wj.mirror_action(self.journal(), outcome="RELEASED")
        self.assertEqual("WRITTEN", result["status"], result)
        text = (self.container / result["path"]).read_text(encoding="utf-8")
        self.assertIn("outcome: RELEASED", text)
        self.assertIn('"summary": "plan ready"', text)

    def test_mirror_never_copies_a_file_that_is_not_the_validated_result(self) -> None:
        secret = self.base / "id_test_key"
        secret.write_text(fake("-----BEGIN OPENSSH PRIVATE", " KEY-----\nKEYBODY\n-----END OPENSSH PRIVATE KEY-----\n"), encoding="utf-8")
        key = secret.read_bytes()
        hard = self.base / "hard.json"
        os.link(secret, hard)
        linkdir = self.base / "linkdir"
        linkdir.symlink_to(self.base, target_is_directory=True)
        invalid = self.journal()
        invalid["artifact"]["validation_status"] = "INVALID"
        cases = {
            "other file, wrong hash": self.journal(path=secret, content=b"anything"),
            "other file, right hash but not a result": self.journal(path=secret, content=key),
            "hard link": self.journal(path=hard, content=key),
            "symlinked parent": self.journal(path=linkdir / "id_test_key", content=key),
            "invalid artifact": invalid,
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                result = self.wj.mirror_action(value, outcome="INVALID")
                self.assertEqual("WRITTEN", result["status"], result)
                text = (self.container / result["path"]).read_text(encoding="utf-8")
                self.assertNotIn("KEYBODY", text)
                self.assertNotIn("Executor result", text)

    def test_result_in_a_symlinked_system_folder_is_still_recorded(self) -> None:
        alias = self.base / "tmp-alias"
        alias.symlink_to(self.base, target_is_directory=True)
        content = json.dumps({"executor_result": {"summary": "through alias"}}).encode("utf-8")
        (self.base / "aliased.json").write_bytes(content)
        result = self.wj.mirror_action(self.journal(path=alias / "aliased.json", content=content), outcome="RELEASED")
        self.assertIn("through alias", (self.container / result["path"]).read_text(encoding="utf-8"))
        link = self.base / "link.json"
        link.symlink_to(self.base / "aliased.json")
        result = self.wj.mirror_action(self.journal(path=link, content=content), outcome="RELEASED")
        self.assertNotIn("through alias", (self.container / result["path"]).read_text(encoding="utf-8"))

    def test_a_fifo_never_blocks_the_mirror(self) -> None:
        fifo = self.base / "final.fifo"
        os.mkfifo(fifo)
        value = self.journal(path=fifo, content=b"x")
        done = threading.Event()
        threading.Thread(target=lambda: (self.wj.mirror_action(value, outcome="INTERRUPTED"), done.set()), daemon=True).start()
        self.assertTrue(done.wait(5), "mirror blocked on a FIFO")

    def test_mirror_action_never_raises(self) -> None:
        stray = self.journal()
        stray["workspace"]["path"] = str(self.base / "nowhere")
        self.assertEqual("SKIPPED", self.wj.mirror_action(stray, outcome="RELEASED")["status"])
        self.assertEqual("SKIPPED", self.wj.mirror_action({}, outcome="RELEASED")["status"])

    def full_journal(self, status: str) -> dict:
        value = self.journal()
        value["action"].update({
            "status": status, "schema_path": ".hermes/orchestration/schemas/EXECUTOR_RESULT_SCHEMA.json", "protocol_version": 2,
            "prompt_hash": "c" * 64, "retry_mode": "FULL_REPLACEMENT", "parent_action_id": None,
            "parent_artifact_path": None, "parent_artifact_sha256": None, "allowed_corrections": [],
        })
        value["fingerprints"] = {"baseline": "d" * 64, "ownership": "e" * 64, "state_before": "f" * 64}
        value["state_commit"] = {
            "state_path": None, "expected_before_hash": "f" * 64, "expected_after_hash": None,
            "committed_after_hash": None, "committed_at": None, "verified": False,
        }
        value["incidents"] = []
        return value

    def actions(self) -> list[str]:
        folder = self.container / "raw" / "articles" / "app-12" / "actions"
        return sorted(p.read_text(encoding="utf-8") for p in folder.iterdir()) if folder.exists() else []

    def test_rollover_reports_the_wiki_record(self) -> None:
        runtime = self.base / "runtime"
        runtime.mkdir()
        value = self.full_journal("RELEASED")
        value["state_commit"].update(state_path=str(runtime / "STATE.md"), expected_after_hash="1" * 64,
                                     committed_after_hash="1" * 64, committed_at="2026-10-07T00:02:00Z", verified=True)
        path = runtime / "ACTION_JOURNAL.json"
        self.aj.atomic_write(path, value)
        result = self.aj.rollover_journal(path, self.repo / "history")
        self.assertEqual("ROLLED_OVER", result["decision"])
        self.assertEqual("WRITTEN", result["wiki"]["status"], result["wiki"])
        self.assertIn('"summary": "plan ready"', (self.container / result["wiki"]["path"]).read_text(encoding="utf-8"))

    def test_archive_interrupted_reports_the_wiki_record(self) -> None:
        runtime = self.base / "runtime"
        runtime.mkdir()
        value = self.full_journal("PROCESS_FINISHED")
        value["artifact"] = {"exists": False, "sha256": None, "validation_status": "PENDING"}
        value["action"]["final_message_path"] = str(runtime / "missing.json")
        path = runtime / "ACTION_JOURNAL.json"
        self.aj.atomic_write(path, value)
        result = self.aj.archive_interrupted_journal(path, self.repo / "history")
        self.assertEqual("INTERRUPTED", result["decision"])
        self.assertEqual("WRITTEN", result["wiki"]["status"], result["wiki"])

    def test_archive_invalid_reports_the_wiki_record_without_the_invalid_body(self) -> None:
        runtime = self.base / "runtime"
        runtime.mkdir()
        artifact = runtime / "final.json"
        artifact.write_text("INVALID_BODY_NOT_RECORDED", encoding="utf-8")
        value = self.full_journal("ARTIFACT_READY")
        value["action"]["final_message_path"] = str(artifact)
        value["action"]["invalid_fields"] = ["executor_result"]
        value["artifact"] = {"exists": True, "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "validation_status": "INVALID"}
        value["process"] = {"started_at": "2026-10-07T00:00:00Z", "finished_at": "2026-10-07T00:01:00Z", "exit_code": 0}
        path = runtime / "ACTION_JOURNAL.json"
        self.aj.atomic_write(path, value)
        result = self.aj.archive_invalid_journal(path, self.repo / "history")
        self.assertEqual("ARCHIVED_INVALID", result["decision"])
        self.assertEqual("WRITTEN", result["wiki"]["status"], result["wiki"])
        text = (self.container / result["wiki"]["path"]).read_text(encoding="utf-8")
        self.assertIn("outcome: INVALID", text)
        self.assertIn("invalid fields: executor_result", text)
        self.assertNotIn("INVALID_BODY_NOT_RECORDED", text)

    def test_prepare_and_block_are_recorded(self) -> None:
        runtime = self.base / "runtime"
        runtime.mkdir()
        path = runtime / "ACTION_JOURNAL.json"
        value = self.full_journal("PREPARED")
        value["artifact"] = {"exists": False, "sha256": None, "validation_status": "PENDING"}
        value["process"] = {"started_at": None, "finished_at": None, "exit_code": None}
        value["action"]["final_message_path"] = str(runtime / "pending.json")
        self.aj.prepare_action(path, value)
        self.assertEqual(1, len(self.actions()))
        self.assertIn("outcome: PREPARED", self.actions()[0])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, self.aj.main(["--journal", str(path), "block"]))
        self.assertTrue(any("outcome: BLOCKED" in text for text in self.actions()))

    def test_record_incident_is_mirrored_into_the_wiki(self) -> None:
        runtime = self.base / "runtime"
        runtime.mkdir()
        (runtime / "ACTION_JOURNAL.json").write_text(json.dumps({"workspace": {"path": str(self.repo)}}), encoding="utf-8")
        identifier = self.aj.record_incident(
            runtime / "INCIDENTS.md", ticket="APP-12", stage="PLAN", action_id="a-1", incident_type="CONTRACT_INVALID",
            summary="invalid", artifact_path="/tmp/final.json", state_reference="STATE.md", recovery="BLOCKED",
            currently_blocking=True, timestamp="2026-10-07T00:00:00Z",
        )
        incidents = list((self.container / "raw" / "articles" / "app-12" / "incidents").iterdir())
        self.assertEqual(1, len(incidents))
        self.assertIn(identifier, incidents[0].read_text(encoding="utf-8"))

    def test_incident_still_recorded_locally_when_the_wiki_is_unavailable(self) -> None:
        runtime = self.base / "runtime"
        runtime.mkdir()
        identifier = self.aj.record_incident(
            runtime / "INCIDENTS.md", ticket="APP-12", stage="PLAN", action_id="a-1", incident_type="STATE_DESYNC",
            summary="desync", artifact_path="/tmp/final.json", state_reference="STATE.md", recovery="RECONCILE",
            currently_blocking=True, timestamp="2026-10-07T00:00:01Z",
        )
        self.assertIn(identifier, (runtime / "INCIDENTS.md").read_text(encoding="utf-8"))


class HookTests(JournalTestCase):
    def turn_payload(self, cwd: Path, **extra) -> dict:
        return {
            "hook_event_name": "post_llm_call", "session_id": "20261007_120000_abc", "cwd": str(cwd),
            "extra": {"user_message": "fix login", "assistant_response": "done", "turn_id": "t1", **extra},
        }

    def sessions(self) -> list[str]:
        folder = self.container / "raw" / "transcripts" / "sessions"
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def test_turn_inside_a_served_worktree_is_recorded(self) -> None:
        result = self.wj.record_turn_event(self.turn_payload(self.repo / "src"))
        self.assertEqual("WRITTEN", result["status"], result)
        text = (self.container / result["path"]).read_text(encoding="utf-8")
        self.assertIn("fix login", text)
        self.assertIn("done", text)

    def test_sessions_outside_the_worktree_are_never_recorded(self) -> None:
        home = Path(os.path.realpath(os.path.expanduser("~")))
        for cwd in (self.repo.parent, self.base, self.vault, home, Path("/")):
            with self.subTest(cwd=str(cwd)):
                self.assertEqual("SKIPPED", self.wj.record_turn_event(self.turn_payload(cwd))["status"])
        self.assertEqual([], self.sessions())

    def test_terminal_cwd_identifies_the_session_folder(self) -> None:
        os.environ["TERMINAL_CWD"] = str(self.repo)
        self.assertEqual("WRITTEN", self.wj.record_turn_event(self.turn_payload(Path("/")))["status"])

    def test_empty_turn_is_skipped(self) -> None:
        result = self.wj.record_turn_event(self.turn_payload(self.repo, user_message="", assistant_response=" "))
        self.assertEqual("SKIPPED", result["status"])

    def test_session_end_is_logged_once_for_a_recorded_session(self) -> None:
        self.wj.record_turn_event(self.turn_payload(self.repo))
        result = self.wj.record_session_end_event({"hook_event_name": "on_session_finalize", "session_id": "20261007_120000_abc", "extra": {"reason": "shutdown"}})
        self.assertEqual("WRITTEN", result["status"], result)
        self.assertIn("session 20261007_120000_abc ended", self.log())
        self.assertEqual("SKIPPED", self.wj.record_session_end_event({"session_id": "unrelated"})["status"])
        again = self.wj.record_session_end_event({"session_id": "20261007_120000_abc", "extra": {"reason": "resume"}})
        self.assertEqual("ALREADY_LOGGED", again["reason"])
        self.assertEqual(1, self.log().count(" ended"))

    def test_hook_main_never_fails_and_prints_an_empty_object(self) -> None:
        def boom(_payload):
            raise RuntimeError("wiki down")

        for stdin in ('{"cwd": "/"}', "not json"):
            output = io.StringIO()
            with mock.patch.object(sys, "stdin", io.StringIO(stdin)), redirect_stdout(output):
                self.assertEqual(0, self.wj.hook_main(boom))
            self.assertEqual("{}", output.getvalue().strip())

    def test_hook_scripts_record_from_the_installed_controller_and_never_block(self) -> None:
        hooks = self.container / ".hermes" / "orchestration" / "hooks"
        env = {key: value for key, value in os.environ.items() if key != "TERMINAL_CWD"}
        runs = (
            ("record-turn.py", self.turn_payload(self.repo)),
            ("record-turn.py", {"cwd": str(self.base)}),
            ("record-session-end.py", {"session_id": "20261007_120000_abc"}),
            ("record-session-end.py", {}),
        )
        for name, payload in runs:
            completed = subprocess.run(
                [sys.executable, "-B", str(hooks / name)], input=json.dumps(payload),
                capture_output=True, text=True, timeout=30, env=env,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual("{}", completed.stdout.strip())
        self.assertEqual(1, len(self.sessions()))
        self.assertIn("ended", self.log())


class CliTests(JournalTestCase):
    def run_cli(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        script = self.container / ".hermes" / "orchestration" / "runtime" / "wiki_journal.py"
        return subprocess.run([sys.executable, "-B", str(script), *args], input=stdin, capture_output=True, text=True, timeout=60)

    def test_cli_records_for_a_served_worktree_from_stdin(self) -> None:
        completed = self.run_cli(
            "record", "--repo", str(self.repo), "--kind", "stage", "--stage", "PLAN", "--ticket", "APP-12",
            "--title", "Plan", "--body-file", "-", "--json", stdin="the plan\n",
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual("WRITTEN", report["status"])
        self.assertIn("the plan", (self.container / report["path"]).read_text(encoding="utf-8"))

    def test_cli_blocks_an_unserved_repository(self) -> None:
        stray = self.base / "stray"
        (stray / ".git").mkdir(parents=True)
        completed = self.run_cli("record", "--repo", str(stray), "--kind", "gate", "--title", "x", "--body", "y", "--json")
        self.assertEqual(2, completed.returncode)
        self.assertEqual("WIKI_WORKSPACE_NOT_REGISTERED", json.loads(completed.stdout)["reason"])
        self.assertFalse((self.container / "raw" / "articles" / "no-ticket").exists())

    def test_cli_without_a_controller_binding_is_blocked(self) -> None:
        (self.container / ".hermes" / "obsidian.json").unlink()
        completed = self.run_cli("record", "--repo", str(self.repo), "--kind", "gate", "--title", "x", "--body", "y", "--json")
        self.assertEqual(2, completed.returncode)
        self.assertEqual("WIKI_BINDING_MISSING", json.loads(completed.stdout)["reason"])


class PolicyTests(unittest.TestCase):
    def test_policies_describe_the_wiki_as_read_and_write(self) -> None:
        loop = (ORCHESTRATION / "policies" / "LOOP_POLICY.md").read_text(encoding="utf-8")
        self.assertIn("Obsidian é leitura **e escrita**", loop)
        self.assertNotIn("OBSIDIAN WRITE PROPOSAL", loop)
        import bounded_run_planner

        self.assertIn("OBSIDIAN_WRITE", bounded_run_planner.AUTO_SAFE)
        self.assertNotIn("OBSIDIAN_WRITE", bounded_run_planner.HUMAN_REQUIRED)
        self.assertNotIn("OBSIDIAN_WRITE", bounded_run_planner.EXTERNAL_MUTATION_ACTIONS)


if __name__ == "__main__":
    unittest.main()
