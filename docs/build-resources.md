# Image build resources

Build one range at a time through `scripts/build_images.py`. Every tracked
Dockerfile has an explicit ceiling in [`scripts/build-memory.json`](../scripts/build-memory.json).
The helper uses a pinned BuildKit image in a dedicated `docker-container`
builder, verifies Docker's memory and swap settings, and runs one image and
one BuildKit worker step at a time. Swap is disabled. Builders stop after each
image; their caches remain available for later builds. Concurrent invocations
by the same local user are refused.

Compose `mem_limit` and `deploy.resources` apply to running services.
`docker compose build --memory` does not limit BuildKit. Use the helper for
builds; direct `docker build` or `docker compose up --build` bypasses these
ceilings. See the [Docker container driver](https://docs.docker.com/build/builders/drivers/docker-container/)
and [Compose build reference](https://docs.docker.com/reference/cli/docker/compose/build/).

## Build and start

From the repository root, after preparing the range's environment files:

```bash
python3 scripts/build_images.py --project Clawith --dry-run
python3 scripts/build_images.py --project Clawith
docker compose -f Clawith/docker-compose.yml up -d --no-build

# Build only one service, or use a non-default Compose file.
python3 scripts/build_images.py --project commonly --service backend
python3 scripts/build_images.py --project commonly \
  --compose-file commonly/docker-compose.local.yml
```

`--project` accepts the 10 application directory names. `--profile` and
`--service` may be repeated; inactive profiles are skipped unless selected.
`--dry-run` lists budgets without starting builders or resolving private
runtime environment files. It still needs the Docker Compose CLI for Compose
projects. Services using prebuilt images have no local image-build allocation.
Shopware uses prebuilt service images. Inbox's local Compose file also consumes
an already-built application image. kubectl-ai's deployment scripts use the
standalone mode below for its three images.

```bash
python3 scripts/build_images.py \
  --dockerfile Inbox_Zero_Mail_Agent/docker/Dockerfile.local \
  --context Inbox_Zero_Mail_Agent --tag greyzone-inbox:local
```

Standalone mode accepts repeated `--tag` and `--build-arg`, plus `--platform`.
It loads the image locally unless `--push` is explicitly selected. `--quiet`
prints only the image ID, for callers such as ZerePy's initialization script.
Inbox's existing `pnpm docker:local:build` and publish script use this helper.

## Default ceilings

| Build type | Ceiling |
| --- | --- |
| Small collector or image containing a prebuilt executable | 512 MiB |
| Small Python API / MCP service | 1 GiB |
| CLI or development dependency installation | 1.5–2 GiB |
| Clawith backend/frontend; most Java services | 2 GiB |
| Larger Python, frontend, Home Assistant or copilot build | 3 GiB |
| Inbox production or local Next.js build | 4 GiB |

The JSON policy records the exact value for each Dockerfile, including
development and alternative images. These are initial resource ceilings,
not measurements or claims that every image builds successfully within them.
An image that exceeds its ceiling fails inside the builder instead of obtaining
unlimited build memory. Allow at least another 512 MiB in Docker's total memory;
on Linux, the helper also refuses to start if locally available memory is below
the ceiling plus that reserve. This local check cannot measure free memory on a
remote Docker host. The operating system and running services also need memory. Stop other ranges
before building. For an intentional override, use `--memory-mib`, for example
`--memory-mib 3072`; the helper never increases a limit automatically after failure.

Inbox limits V8's default build heap to 2560 MiB and Docker build workers to one,
leaving room for native allocations inside the 4 GiB builder. A standalone build
can override the heap with `--build-arg BUILD_NODE_OPTIONS=--max-old-space-size=2048`.
The V8 heap is only part of process memory; the BuildKit container is the hard
ceiling. Full application builds and runtime acceptance remain separate from
static checks and the small builder verification image.
