"""Behavior of runtime/redaction.py: credential shapes are masked in linear time.

Pure unit tests: the module is imported straight from the template runtime,
no vault and no controller copy. ``test_wiki_journal`` proves record() calls it.
"""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
_spec = importlib.util.spec_from_file_location("redaction", RUNTIME / "redaction.py")
redaction = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(redaction)

MAX_RECORD_BYTES = 512 * 1024  # wiki_journal.MAX_RECORD_BYTES: record() never redacts more than this


def fake(prefix: str, body: str) -> str:
    """Build a credential-shaped test value without a literal secret in the source."""
    return prefix + body


class RedactionTests(unittest.TestCase):
    def test_only_redact_is_public(self) -> None:
        self.assertEqual(["redact"], redaction.__all__)

    def test_common_credential_shapes_are_redacted(self) -> None:
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
        text = redaction.redact("\n".join(sample for sample, _ in samples.values()))
        for name, (_, value) in samples.items():
            with self.subTest(secret=name):
                self.assertNotIn(value, text)
        self.assertIn("[REDACTED]", text)

    def test_redaction_stays_linear_on_adversarial_input(self) -> None:
        import time

        size = MAX_RECORD_BYTES
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
                redaction.redact(text)
                self.assertLess(time.monotonic() - started, 3.0)

    def test_every_redaction_pattern_is_linear_on_repeated_prefixes(self) -> None:
        """Each credential prefix repeated with each separator: quadratic patterns take seconds here."""
        import time

        size = 128 * 1024
        prefixes = (
            "eyJ", "sk-", "github_pat_", "ghp_", "glpat-", "hf_", "whsec_", "xoxb-", "AIza", "AKIA",
            fake("-----BEGIN RSA PRIVATE", " KEY-----"), "a://", "https://u:", "Bearer ", "Cookie:", "AccountKey=",
            "--user=", "--password ", "sshpass -p", "mysql -p", "password is ", '"api_key":', "x_token_y=", "a",
        )
        separators = ("-", ".", "_", "+", "/", "=", ":", "@", " ", '"', "\\", "\n")
        slowest = (0.0, "")
        for prefix in prefixes:
            for separator in separators:
                unit = prefix + separator
                text = (unit * (size // len(unit) + 1))[:size]
                started = time.monotonic()
                redaction.redact(text)
                slowest = max(slowest, (time.monotonic() - started, repr(unit)))
        self.assertLess(slowest[0], 1.5, slowest)

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
                    self.assertNotIn(secret, redaction.redact(f"{context}{token} tail"))

    def test_a_jwt_is_redacted_whole_after_a_hyphen(self) -> None:
        """A hyphen before the token must not leave the payload and signature in clear."""
        jwt = fake("eyJhbGciOiJIUzI1NiJ9.", "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")
        signature = "dozjgNryP4J3jVmNHl0w5N"
        for context in ("session-", "x-auth-", "refresh-token-", "Set-Cookie--", "jwt-", "sk-", "AIza-", "glpat-", "-", "a-b-"):
            with self.subTest(context=context):
                redacted = redaction.redact(context + jwt)
                self.assertNotIn(signature, redacted)
                self.assertNotIn("eyJzdWIiOiIxMjM0NTY3ODkw", redacted)
        hyphen_in_header = fake("eyJhbG-iOiJIUzI1NiJ9.", "eyJzdWIiOiIxMjM0NTY3ODkwIn0.") + signature
        self.assertNotIn(signature, redaction.redact("token " + hyphen_in_header))

    def test_large_and_multi_segment_jwts_are_redacted_whole(self) -> None:
        """Real tokens exceed any fixed bound: x5c headers, big payloads, JWE, unsigned two-part tokens."""
        import base64

        def part(value: dict) -> str:
            return base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).decode("ascii").rstrip("=")

        signature = fake("dozjgNryP4J3jVmNHl0w5N7ZbQZ8", "XrVqKkLmNoPqRsTuVwXyZ0123")
        shapes = {
            "payload_12k": (part({"alg": "RS256"}), part({"sub": "1", "data": "B" * 9000})),
            "x5c_header": (part({"alg": "RS256", "x5c": ["A" * 2800]}), part({"sub": "1", "data": "B" * 1100})),
            "huge_both": (part({"alg": "RS256", "x5c": ["A" * 9000]}), part({"sub": "1", "data": "B" * 9000})),
        }
        for name, (header, payload) in shapes.items():
            for context in ("", "Bearer ", "x-auth-", "?jwt=", "sk-"):
                with self.subTest(shape=name, context=context):
                    redacted = redaction.redact(f"{context}{header}.{payload}.{signature} tail")
                    for segment in (header, payload, signature):
                        self.assertNotIn(segment[-24:], redacted)
        jwe = part({"alg": "RSA-OAEP", "enc": "A256GCM"}) + ".encryptedKEY0123.initVECTOR012.CIPHERtext0123456.authTAG012345"
        self.assertEqual("x [REDACTED] y", redaction.redact(f"x {jwe} y"))
        unsigned = part({"alg": "none"}) + "." + part({"sub": "x"})
        self.assertEqual("t [REDACTED]", redaction.redact("t " + unsigned))
        direct_jwe = part({"alg": "dir", "enc": "A256GCM"}) + "..initVECTOR0123.CIPHERtext0123456789.authTAG0123456"
        detached = part({"alg": "HS256", "b64": False}) + "..detachedSIG0123456789"
        padded = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode("ascii") + "." + base64.urlsafe_b64encode(b'{"sub":"12"}').decode("ascii") + ".paddedSIG0123456789=="
        for token, secret in ((direct_jwe, "CIPHERtext0123456789"), (detached, "detachedSIG0123456789"), (padded, "paddedSIG0123456789")):
            for context in ("got ", "x_", "ACCESS_TOKEN_", "next-auth.session-token=", '{"t":"'):
                with self.subTest(token=token[:12], context=context):
                    self.assertNotIn(secret, redaction.redact(f"{context}{token}."))
        for ordinary in ("file.eyJ.txt", "see docs.example.com/eyJ", "version 1.2.3", "eyJ is a prefix", "eyJabcdefgh..txt", "a=b.c=d", "foo_eyJ.bar"):
            self.assertEqual(ordinary, redaction.redact(ordinary))

    def test_url_password_after_punctuation_and_sshpass_after_brackets_are_redacted(self) -> None:
        for context in ("-", ".", "+", "(", '"', "="):
            with self.subTest(context=context):
                self.assertNotIn("urlpassword9", redaction.redact(context + fake("postgres://user:", "urlpassword9@db/app")))
        for context in ("(", '"', ",", "=", "[", "'", ":", "/", ".", "-", "+", "<", "|", "*"):
            with self.subTest(sshpass=context):
                self.assertNotIn("sshsecret7", redaction.redact(context + fake("sshpass -p ", "sshsecret7 ssh h")))

    def test_redaction_keeps_ordinary_values(self) -> None:
        text = redaction.redact('max_tokens: 4096\ntoken_count = 12\n"auth": true\nTOTAL=7\nport: 5432\nhttps://example.com/a@b')
        self.assertIn("port: 5432", text)
        self.assertIn("https://example.com/a@b", text)
        self.assertEqual("see https://docs.x/y and mail me@x.com", redaction.redact("see https://docs.x/y and mail me@x.com"))
        self.assertIn("max_tokens: 4096", text)
        self.assertIn("token_count = 12", text)
        self.assertIn('"auth": true', text)

if __name__ == "__main__":
    unittest.main()
