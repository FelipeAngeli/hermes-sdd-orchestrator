# Image build

Applies to Dockerfiles built with BuildKit (the default builder in current Docker Engine releases). Confirm instruction and flag support against the Dockerfile reference and the detected Docker/Buildx version: `https://docs.docker.com/reference/dockerfile/` and `https://docs.docker.com/build/building/best-practices/`.

## Diagnose first

- Reuse the project's existing Dockerfile and build script when present; change only what the slice needs.
- Derive the base image and runtime version from the project's runtime version file or manifest, not from a guess.
- Identify the build output (compiled binary, bundle, wheel, jar, static files) that the final image actually needs.

## Multi-stage builds

- Use separate stages: one with build tools that produces the artifact, and a final stage that copies only the artifact and runtime dependencies (`COPY --from=<stage>`).
- The final stage contains no compiler, package-manager cache, test files or source that the runtime does not need.
- Name stages (`FROM ... AS build`) and build a specific target in CI when several stages exist.

## Reproducibility and size

- Pin the base image to a specific version tag, and to a digest (`image:tag@sha256:...`) when the project requires byte-for-byte reproducibility; a digest also requires an update process for security patches.
- Install dependencies from the project's lockfile with the package manager's locked or frozen mode.
- Choose a smaller base (slim, distroless or minimal) only when the application's runtime needs are met; verify that native libraries and certificates are present.
- Remove package-manager caches in the same layer that creates them, or use cache mounts so they never enter a layer.

## Build cache

- Order instructions from least to most frequently changing: copy dependency manifests and lockfiles, install dependencies, then copy source.
- Use `RUN --mount=type=cache,target=<cache dir>` for package-manager caches when the builder supports it.
- Keep the build context small with `.dockerignore`; every file in the context can invalidate cache and can leak.

## Non-root user and permissions

- Create or select an unprivileged user and switch to it with `USER` in the final stage; the application never needs root to serve requests on an unprivileged port.
- Give that user ownership only of paths it must write (`COPY --chown=...` or explicit `chown`), and keep application code read-only for it where possible.
- Do not add tools (shells, package managers, debuggers) to the final image unless the runtime or the project's operational practice requires them.

## .dockerignore

Exclude at least: VCS directories (`.git`), local environment and secret files (`.env`, `*.pem`, credential files), dependency directories rebuilt in the image, build outputs, test reports, logs and editor files. Prefer an allow-list (`*` then `!` exceptions) when the context is large or sensitive. Verify by building and listing the context or the final image filesystem.

## Build-time secrets

- Never pass secrets through `ARG` or `ENV`; values persist in image metadata or history. Docker's build checks flag this pattern.
- Use BuildKit secret mounts: `RUN --mount=type=secret,id=<id> ...` with `docker build --secret id=<id>,src=<file>` (or `env=<VAR>`); the secret is available only to that `RUN` instruction and is not written to a layer.
- For private Git dependencies, prefer `--mount=type=ssh` over copying keys.

## Entrypoint and signals

- Use the exec form (`CMD ["executable", "arg"]`, `ENTRYPOINT [...]`) so the application runs as PID 1 and receives stop signals; a shell form wraps it in a shell that may not forward signals.
- When an entrypoint script is needed, end it with `exec "$@"` or `exec <command>`.
- The application handles the stop signal with a graceful shutdown within the platform's stop timeout.
- `EXPOSE` documents the port; it does not publish it.
- Add a `HEALTHCHECK` only when the runtime platform uses it and the image contains the probe command; many orchestrators define their own probes.

## Verification

- `docker build` (or `docker buildx build`) with the project's arguments succeeds from a clean checkout.
- `docker image inspect -f '{{.Config.User}}' <image>` names the unprivileged user, and `docker run --rm --entrypoint id <image> -u` reports a non-zero UID when the image contains `id` (`--entrypoint` overrides any `ENTRYPOINT`).
- `docker history --no-trunc <image>` and the image filesystem contain no secret or excluded file.
- The image starts, answers its health or smoke check and stops cleanly on `docker stop`.
