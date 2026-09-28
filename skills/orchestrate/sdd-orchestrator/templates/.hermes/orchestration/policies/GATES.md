# Gate Policy — configure for this repository

This file is intentionally language-neutral. Before starting work, replace every `UNCONFIGURED` command with a command that exists and passes in **this** repository. Do not disable a failing project CI automatically.

```yaml
project_ci_policy:
  enabled: false
  status_when_skipped: DISABLED_BY_PROJECT_POLICY
  reason: Configure this policy explicitly for the target repository before declaring DONE.
  temporary: false
```

## Required order

1. TDD RED/GREEN for every implementation slice
2. Focused tests
3. Formatter restricted to files owned and changed by the agent
4. Static analysis
5. REVIEW
6. CI only when `project_ci_policy.enabled: true`
7. DONE

The order and gate names are fixed for every language. Only the commands change.

## How to configure

1. Run the read-only detector from the repository root:

   ```text
   python3 .hermes/orchestration/runtime/detect_stack.py --target .
   ```

   It reports each ecosystem with the manifest that proves it, the CI providers present, the instruction files to read (`AGENTS.md`, `CLAUDE.md`, ADRs…) and a *suggested* command per gate. A gate without evidence is reported as `UNKNOWN`; the detector never guesses.
2. Prefer the project's own entry points over raw tools: `Makefile`/`justfile`/`Taskfile` targets, `package.json` scripts, CI job steps, or commands documented in `AGENTS.md`/`CONTRIBUTING.md`. What CI runs is the strongest evidence.
3. Run each chosen command once, in the foreground with its timeout, and record its exit code. A command that was never run is not configured.
4. Write the verified commands in the table below. `{files}` is replaced by the controller with the agent-owned changed files; a formatter without `{files}` may be used only if it cannot touch protected files.
5. Leave a gate as `UNCONFIGURED` only with a written reason. An `UNCONFIGURED` required gate blocks DONE; it never counts as `PASS`.

## Target-project commands

| Gate | Command | Executor | Timeout |
| --- | --- | --- | --- |
| TDD RED | `UNCONFIGURED` — focused test command for one test/file | worker | 300 s |
| TDD GREEN | `UNCONFIGURED` — same command as TDD RED | worker | 300 s |
| Focused tests | `UNCONFIGURED` — tests covering the changed scope | worker/host | 300 s |
| Format | `UNCONFIGURED` — formatter restricted to `{files}` | host | 120 s |
| Analyze | `UNCONFIGURED` — linter / type checker / compiler warnings | host | 300 s |
| CI | `UNCONFIGURED` — local CI entry point, if any | host | 900 s |

### Monorepos

When the detector reports several ecosystems or member paths, add one row per member with its working directory (for example `cd apps/web && pnpm run test`). A slice uses only the rows of the members it changes; REVIEW and CI use every affected member.

## Reference commands by ecosystem

Starting points only. The detector refines them from lockfiles and tool configuration; the repository's own scripts always win.

| Ecosystem | Focused tests | Format (`{files}`) | Analyze |
| --- | --- | --- | --- |
| Node / TypeScript | `<pm> run test -- <path>` | `<pm> exec prettier --write {files}` | `<pm> run lint` / `tsc --noEmit` |
| Python | `python -m pytest <path>` | `ruff format {files}` / `black {files}` | `ruff check .` / `mypy .` |
| Go | `go test ./<pkg>/...` | `gofmt -w {files}` | `go vet ./...` |
| Rust | `cargo test <name>` | `rustfmt {files}` | `cargo clippy --all-targets` |
| Java / Kotlin (Gradle) | `./gradlew test --tests <Class>` | `./gradlew spotlessApply` / `ktlintFormat` | `./gradlew detekt` / `check` |
| Java (Maven) | `./mvnw test -Dtest=<Class>` | `./mvnw spotless:apply` | `./mvnw checkstyle:check` |
| C# / .NET | `dotnet test --filter <Name>` | `dotnet format --include {files}` | `dotnet build -warnaserror` |
| Ruby | `bundle exec rspec <path>` | `bundle exec rubocop -a {files}` | `bundle exec rubocop` |
| PHP | `vendor/bin/phpunit <path>` | `vendor/bin/php-cs-fixer fix {files}` | `vendor/bin/phpstan analyse` |
| Elixir | `mix test <path>` | `mix format {files}` | `mix credo` |
| Swift | `swift test --filter <Name>` | `swift-format -i {files}` | `swiftlint` |
| C / C++ (CMake) | `ctest --test-dir build -R <name>` | `clang-format -i {files}` | `clang-tidy {files}` |
| Dart / Flutter | `[fvm] flutter test <path>` | `[fvm] dart format {files}` | `[fvm] flutter analyze` |

## Completion rules

When CI is enabled, DONE requires focused tests, format, analysis, review, and CI to pass. When disabled by the explicit policy above, record `ci: DISABLED_BY_PROJECT_POLICY`; this does not convert an already-failed CI execution into a pass.

A repository with no formatter or no static analyzer must say so in the table (for example `NOT_APPLICABLE — no formatter configured in this repository`). The controller may then record that gate as `PASS` only with that written justification and explicit human confirmation stored in STATE. Absence of evidence is never silently treated as a pass.

A timeout is `TIMEOUT`, must be recorded in STATE, and blocks advancement. Do not use polling or background waits.
