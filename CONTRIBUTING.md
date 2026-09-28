# Contributing

Thanks for helping improve the Hermes SDD Orchestrator. This is an open-source project under the [MIT License](LICENSE); issues, ideas and pull requests are welcome from anyone.

## Ways to contribute

| You want to… | Open |
| --- | --- |
| Propose an improvement or new capability | [Feature request](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/issues/new?template=feature_request.yml) |
| Add or improve support for a language/toolchain | [Ecosystem support](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/issues/new?template=ecosystem_support.yml) |
| Report something that does not work | [Bug report](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/issues/new?template=bug_report.yml) |
| Ask a question or discuss an idea | A [blank issue](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/issues/new) labeled `question` |
| Report a vulnerability | See [SECURITY.md](SECURITY.md) — do **not** open a public issue |

Search existing issues first; a 👍 on an existing issue helps prioritize it more than a duplicate.

## Good first contributions

- **New ecosystem in `detect_stack.py`**: add a manifest, an analyzer that suggests commands only from real evidence, and a test in `tests/test_detect_stack.py`.
- **New reference row in `policies/GATES.md`**.
- **Documentation fixes** where a README or policy no longer matches the code.

Issues labeled [`good first issue`](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/labels/good%20first%20issue) and [`help wanted`](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/labels/help%20wanted) are ready to pick up.

## Development

Requirements: Git and Python 3.10+ (`jsonschema` for the bounded-run tools).

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/tests -p 'test_*.py'
```

Both suites must pass before a pull request is reviewed.

## Project rules

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing structure; all documentation starts at [docs/README.md](docs/README.md). In short:

- **Docs ship with the change.** Every orchestration change updates its owning page and `CHANGELOG.md` — enforced by tests, the hook and CI. See [Maintaining the docs](docs/maintaining-docs.md).
- **Test first.** Behavior changes start with a failing test (RED → GREEN).
- **Language-neutral payload.** Ecosystem knowledge lives only in `runtime/detect_stack.py` and the reference table of `policies/GATES.md`.
- **Evidence over guesses.** The detector suggests a command only when repository evidence supports it.
- **Safe installer.** It stays idempotent and never modifies tracked files of the target.
- **Controller authority.** Agents and sub-agents never own STATE, transitions or recursive dispatch.

## Pull requests

1. Fork and create one branch per improvement from `main`, named `feat/…`, `fix/…`, `docs/…`, `refactor/…`, `test/…` or `chore/…`. Never commit to `main` directly.
2. Keep the change focused on one improvement. If it addresses a tracked issue, reference it (`Closes #123`); work requested directly by a maintainer may state `Issue: not applicable — direct request` instead.
3. Use [Conventional Commits](https://www.conventionalcommits.org/).
4. Add a `CHANGELOG.md` entry under `## Unreleased`. Maintainers cut the version with `tools/release.py`; see [Branches and versions](docs/maintaining-docs.md#branches-and-versions).
5. Fill in the pull request template, including tests and `pr-reviewer` result.

By contributing, you agree that your contributions are licensed under the MIT License and that you will follow the [Code of Conduct](CODE_OF_CONDUCT.md).
