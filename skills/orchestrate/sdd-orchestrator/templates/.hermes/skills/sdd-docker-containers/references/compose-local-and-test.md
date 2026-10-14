# Compose for local development and tests

Applies to Docker Compose v2 (`docker compose`) and the Compose Specification. Confirm attribute support against `https://docs.docker.com/reference/compose-file/` and the detected `docker compose version`. The top-level `version` field is obsolete in the Compose Specification: current Compose ignores it and warns; do not add it, and remove it only when the project agrees.

## Scope

Compose here runs the application and its dependencies on a developer machine or a CI runner. It is not assumed to be a production deployment mechanism; see `safety-and-deployment.md`.

## Services and dependencies

- Model each dependency the application needs locally (database, cache, broker, fake external service) as a service with an image version matching what the project targets, not `latest`.
- Define a `healthcheck` for each dependency with a command that proves readiness (for example a database's own readiness probe), with `interval`, `timeout`, `retries` and `start_period` tuned to its startup.
- Use the long form of `depends_on` with `condition: service_healthy` for services that must be ready, and `condition: service_completed_successfully` for one-shot jobs such as migrations or seeding. The short form only orders startup; it does not wait for readiness.
- The application still retries its connections at startup; Compose ordering does not cover restarts of a dependency.

## Networks and ports

- Services reach each other by service name on the project's default network; add custom networks only to isolate groups of services.
- Publish only the ports a developer or test runner must reach from the host, and bind them to `127.0.0.1` when they should not be exposed to the local network.

## Volumes

- Use a named volume for data that should survive container recreation (database files); use a bind mount for source code in development when hot reload is needed.
- Know that bind mounts expose host files to the container with host permissions; avoid mounting the whole repository or home directory when a subdirectory suffices.
- For tests, prefer disposable data (an anonymous or test-only named volume, or `tmpfs`) so each run starts from a known state.

## Configuration and credentials

- Keep development defaults non-secret and documented in a committed example file; real values live in an uncommitted `.env` or `env_file` listed in `.gitignore` and `.dockerignore`.
- Remember that the `.env` file next to the Compose file is used for variable interpolation in the Compose file, while `env_file` sets container environment; they are different mechanisms.
- For sensitive values, prefer Compose `secrets`, which are mounted as files under `/run/secrets/<name>` inside the container, when the application can read a file; environment variables can leak through process listings, logs and `docker inspect`.
- Run `docker compose config` to check interpolation; its output contains resolved values, so do not paste it into logs or tickets when it includes secrets.

## Profiles and overrides

- Use `profiles` to keep optional services (tools, admin UIs, load generators) off by default.
- Use override files (`compose.override.yaml` or explicit `-f` files) for development-only settings such as bind mounts and debug ports, keeping the base file closer to what tests use.

## Tests

- Start dependencies with `docker compose up -d --wait` when the Compose version supports `--wait`, or poll health status, before running tests.
- Use a dedicated project name (`-p` or `COMPOSE_PROJECT_NAME`) for test runs so they cannot reuse or delete a developer's volumes.
- Tear down only what the test run created.

## Verification

- `docker compose config` succeeds for the detected Compose version.
- `docker compose up` brings every dependency to healthy, and the application's health or smoke check passes.
- No secret is committed; example files contain placeholders only.
