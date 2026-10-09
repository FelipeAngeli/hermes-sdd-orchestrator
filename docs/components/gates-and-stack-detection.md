# Gates and stack detection

[Docs index](../README.md) · Previous: [Skill and installer](skill-and-installer.md) · Related: [FSM and bounded loop](fsm-and-loop.md), [Contracts and schemas](contracts-and-schemas.md)

**Files:** `policies/GATES.md`, `runtime/detect_stack.py`.

This is the **only** part of the orchestrator that depends on the project's language. Everything else names gates by role.

## Gate order (`GATES.md`)

The order is fixed for every language:

1. TDD RED/GREEN for every implementation slice
2. Focused tests (`TEST_FOCUSED`)
3. Formatter restricted to agent-owned changed files (`FORMAT_CHANGED_FILES`)
4. Static analysis (`ANALYZE`)
5. REVIEW
6. CI, only when `project_ci_policy.enabled: true`
7. DONE

The FSM actions and their preconditions live in the planner; see [FSM and bounded loop](fsm-and-loop.md#actions). The review result reports gate states as `PASS`, `FAIL`, `TIMEOUT`, `BLOCKED`, `PENDING` and, for CI only, `NOT_APPLICABLE` or `DISABLED_BY_PROJECT_POLICY`; see [Contracts and schemas](contracts-and-schemas.md#review-result).

Rules:

- A gate still `UNCONFIGURED` blocks DONE. It never counts as `PASS`.
- `NOT_APPLICABLE` (for example, no formatter exists) requires a written reason and human confirmation recorded in STATE.
- A timeout is `TIMEOUT` and blocks advancement. Disabled CI is recorded as `DISABLED_BY_PROJECT_POLICY`, which is never a pass.
- In a monorepo, add one row per member with its working directory. A slice uses only the rows of the members it changes.
- Gates run on the host, with the user's privileges, the repository as working directory and no sandbox. A gate executes code the worker wrote: the test runner imports its test files and fixtures (a `conftest.py`, for example), a build or lint step loads its configuration. That code can reach anything the user can, including `GATES.md`, `EXECUTORS.md`, STATE and the journal in the Obsidian container; the container only keeps the *worker process* out. This is inherent to running tests and is not sandboxed: run gates only where you would run the repository's code, and in REVIEW read the diff of every test, fixture, build and tool-configuration file the worker changed before a gate runs it.

## Configuring a project

1. Run `python3 .hermes/orchestration/runtime/detect_stack.py --target .`. The installer's dry run already includes the same report under `stack`.
2. Prefer the project's own entry points: Makefile/justfile targets, `package.json` scripts, CI steps, or commands from `AGENTS.md`/`CONTRIBUTING.md`.
3. Run each command once in the foreground and record its exit code.
4. Write the verified commands into the `GATES.md` table. `{files}` is replaced by the agent-owned changed files, one argument each; a path segment starting with `-` is refused (`AGENT_OWNED_PATH_UNSAFE`), never passed as an option. `sdd.py start` pins the SHA-256 of `GATES.md` and `EXECUTORS.md`; editing either during a demand stops the next gate with `CONTROLLER_POLICY_CHANGED_DURING_DEMAND` until the user confirms that file ([`sdd.py confirm-policy --name gates|executors`](fsm-and-loop.md)).

## `detect_stack.py`

Read-only and standard-library only. It walks the repository root and two levels below (for monorepos), skipping dependency and build directories (`node_modules`, `.venv`, `vendor`, `target`, `build`, `.dart_tool`, …).

```text
python3 .hermes/orchestration/runtime/detect_stack.py --target <dir> [--json]
```

| Flag | Meaning |
| --- | --- |
| `--target` | Directory to inspect (default `.`). |
| `--json` | JSON report instead of text. |

Report fields: `status` (`DETECTED_UNVERIFIED` or `UNKNOWN`), `ecosystems[]` (`ecosystem`, `path`, `evidence`, `suggested_gates`, plus `package_manager` or `flavor` where relevant), `ci[]`, `instruction_files[]` and `note`.

### Supported ecosystems

A command is suggested only when the listed evidence exists. Otherwise the gate is `null`.

| `ecosystem` | Detected by | Refined by |
| --- | --- | --- |
| `node` | `package.json` | lockfile picks `pnpm` / `yarn` / `bun` / `npm`; only declared `scripts` (`test`, `format`/`fmt`, `lint`/`typecheck`/`check`) |
| `python` | `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `Pipfile` | pytest, ruff, black, mypy, flake8 configuration |
| `go` | `go.mod` | none |
| `rust` | `Cargo.toml` | none |
| `jvm-maven` | `pom.xml` | `mvnw` wrapper, spotless, checkstyle |
| `jvm-gradle` | `build.gradle.kts`, `build.gradle` | `gradlew` wrapper, spotless, ktlint, detekt |
| `ruby` | `Gemfile` | `.rspec`/`spec`, `Rakefile`, `.rubocop.yml` |
| `php` | `composer.json` | composer `test` script, phpunit, phpstan, php-cs-fixer |
| `elixir` | `mix.exs` | credo |
| `swift` | `Package.swift` | swift-format, swiftformat, swiftlint |
| `dotnet` | `*.csproj`, `*.fsproj`, `*.sln` | none |
| `cmake` | `CMakeLists.txt` | `.clang-format`, `.clang-tidy` |
| `dart` | `pubspec.yaml` | Flutter SDK dependency, FVM (`.fvmrc`/`.fvm`) prefix |

CI providers: GitHub Actions, GitLab CI, CircleCI, Azure Pipelines, Bitbucket Pipelines, Jenkins, Buildkite, Travis CI.

**Adding an ecosystem:** add an analyzer and an `ECOSYSTEMS` entry in `detect_stack.py`, a test in `tests/test_detect_stack.py`, a row in the table above, and a reference row in `GATES.md`. [`tests/test_docs.py`](testing.md#skill-suite-tests) fails if the row above is missing.
