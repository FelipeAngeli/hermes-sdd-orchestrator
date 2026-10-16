---
name: sdd-docker-containers
description: Guide Dockerfile and Compose work with safe local checks.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [docker, containers, dockerfile, compose, build, security]
    related_skills: [sdd-backend-engineering, sdd-database-design-migrations, sdd-release-readiness]
---

# SDD Docker and Containers

Project-local playbook for application container work: Dockerfiles, `.dockerignore`, image builds and Docker Compose files for local development and tests. The project's existing files, scripts, CI and deployment platform are authoritative; this playbook does not choose a deployment platform. It keeps three concerns apart: **build** (producing an image), **local run** (Compose for development and tests) and **deployment** (running the image on the project's platform).

## When to Use

Use when a slice creates or changes a Dockerfile, `.dockerignore`, Compose file, build arguments, container health checks, or the scripts and CI steps that build or run containers.

Do not use to choose or operate a production orchestrator, to run containers against shared or production resources without authorization, or for application code inside the container (`sdd-backend-engineering`).

## Prerequisites

- Diagnose before editing: list existing Dockerfiles, Compose files, `.dockerignore`, build scripts, CI jobs, runtime version files and the deployment target named by the project.
- Record the installed Docker Engine, Buildx and Compose versions when commands are available (`docker version`, `docker buildx version`, `docker compose version`); otherwise record the versions as unknown and avoid version-specific syntax.
- Identify the application's runtime, build command, start command, listening port, required configuration and writable paths from the repository.

## Reference Routing

| Concern | Read |
| --- | --- |
| Dockerfile, multi-stage builds, reproducibility, cache, non-root user, `.dockerignore`, build secrets | `references/image-build.md` |
| Compose for development and tests: services, health checks, dependencies, networks, volumes, configuration | `references/compose-local-and-test.md` |
| Destructive commands, shared environments, deployment boundary | `references/safety-and-deployment.md` |

## Procedure

1. **Diagnose the project.** Map existing container files and the commands that use them. Done when each change extends an existing file or a missing file is recorded as a gap the slice fills.
2. **Separate build, local run and deployment.** State which of the three the slice changes. Compose is not assumed suitable for production; deployment changes follow the project's platform. Done when no file mixes concerns without a stated reason.
3. **Build a minimal, reproducible image.** Apply `references/image-build.md`. Done when the image builds from a clean checkout with the project command and runs as a non-root user.
4. **Keep secrets out of images and files.** Use build secrets and runtime injection; never `ARG`, `ENV`, `COPY` or committed files with real credentials. Done when image history and the diff contain no secret.
5. **Make local run verifiable.** Apply `references/compose-local-and-test.md`. Done when dependencies start healthy and the application answers its health or smoke check.
6. **Stay within authorization.** Apply `references/safety-and-deployment.md` before any command that deletes data or touches shared resources. Done when every destructive command has explicit authorization.

## Pitfalls

- Do not copy a generic Dockerfile; base image, user, paths and commands come from the project.
- Do not rely on a mutable tag for reproducibility; record when a digest pin is used.
- Do not treat a container that started as healthy; check readiness.
- Do not run `docker compose down -v`, prune commands or volume deletion without authorization.

## Verification

- The project's build command succeeds from a clean checkout; the image runs as non-root.
- `.dockerignore` excludes secrets, local environment files, VCS data and build artifacts.
- `docker compose config` renders the Compose file without errors for the detected version.
- Health or smoke checks pass locally; no secret appears in image history, logs or the diff.
