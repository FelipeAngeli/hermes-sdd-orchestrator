# Safety limits and the deployment boundary

## Destructive commands

These commands delete data or state that may not belong to the current task. Run them only with explicit authorization naming the command and its scope, and state what will be lost before running:

- `docker compose down -v` (removes named volumes declared in the Compose file and anonymous volumes attached to its containers);
- `docker volume rm`, `docker volume prune`;
- `docker system prune` (with or without `--volumes` and `-a`), `docker image prune -a`, `docker builder prune`;
- `docker rm -f` or `docker stop` on containers the task did not create;
- any command against a remote Docker context or host.

Prefer the narrowest action: stop and remove only the current project's containers (`docker compose down` without `-v`), and use a dedicated project name for disposable test stacks. Before deleting a volume, check whether it holds a developer's local data and whether a backup exists.

## Shared environments

- Check the active Docker context (`docker context show`) before running commands; a context can point at a remote or shared engine.
- Never point a local Compose stack at shared or production databases, brokers or storage without explicit authorization.
- Never push an image to a registry, change tags others consume or log in to a registry with personal credentials as part of a slice unless the task authorizes it.

## Deployment boundary

- The deployment platform (an orchestrator such as Kubernetes or a managed container service) and its manifests are authoritative for production: replicas, resource limits, health probes, secrets, networking and rollout strategy.
- Compose is not assumed suitable for production. When the project does deploy with Compose, follow the project's documented procedure and treat changes as deployment changes that need their own review.
- Set resource limits (memory and CPU) for production on the deployment platform; a container without a memory limit can consume host memory until the kernel kills processes. Local Compose limits are useful to reproduce constraints but do not prove production behavior.
- Image provenance for release (tagging scheme, signing, scanning, SBOM) follows the project's release process; apply `sdd-release-readiness` near DONE.

## Verification

- Every destructive or shared-environment command in the slice cites the authorization that allowed it.
- No registry push, remote context or production resource was used without authorization.
- Deployment-facing changes name the platform and its owner, or are recorded as out of scope.
