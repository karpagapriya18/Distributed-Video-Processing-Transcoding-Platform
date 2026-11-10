# Frameflow — Distributed Video Processing

A Python 3.12 / FastAPI platform with seven independently runnable services, a React + TypeScript dashboard, service-owned MySQL schemas, RabbitMQ processing commands, Redis progress caching, and FFmpeg media processing.

Upload a video, select 360p / 720p / 1080p, start processing, and watch status, attempts, thumbnails and playable MP4s appear in the dashboard. Processing starts explicitly through the dashboard or API.

## Start with Docker

Requirements: Docker Desktop with Linux containers and Docker Compose v2. Allocate approximately 4 GB of RAM; image builds require internet access.

```sh
cp .env.example .env
docker compose up --build -d
docker compose ps
```

On PowerShell, use `Copy-Item .env.example .env` for the first command. Run all commands from this directory.

- Dashboard: http://localhost:3000
- Gateway OpenAPI / Swagger: http://localhost:8000/docs
- Gateway ReDoc: http://localhost:8000/redoc
- RabbitMQ management: http://localhost:15672 (`platform` / `platform`)

Enter the `API_KEY` from `.env` into the dashboard and click **Connect**. The development value is `local-demo-key-change-me`. Keys are stored in browser session storage. Upload a short MP4, select it, and click **Start processing**. Refreshing the page preserves access to persisted jobs and outputs.

The first startup initializes five MySQL databases and applies Alembic migrations. Only the gateway, frontend, and loopback RabbitMQ management interface expose host ports. Services run as an unprivileged user; a one-time container sets media volume ownership.

## Verify

```sh
# Unit, service contract, real encoder, and full running-stack E2E tests:
docker compose --profile test run --build --rm tests

# Local backend tests (SQLite databases; external infrastructure is not required):
python -m venv .venv
# Activate .venv using your shell's activation command, then:
python -m pip install -r requirements.txt
python -m pytest -q

# Frontend type checking and production build:
cd frontend
npm ci
npm run build
```

Local tests skip two Docker E2E cases unless `RUN_E2E=1`. The real encoder test skips if FFmpeg is not on PATH; set `TEST_FFMPEG` to an absolute executable path to enable it. The tests profile installs FFmpeg and runs all cases. The E2E test creates a synthetic video, uploads it through the gateway, sends a duplicate RabbitMQ command, waits for all resolutions and a thumbnail, checks persisted attempts, downloads media, exercises byte ranges, and rejects expired links.

See [verification.md](docs/verification.md) for the checks performed in the delivery environment and their limitations.

## Project map

| Path | Purpose |
| --- | --- |
| `services/gateway.py` | Public request validation, authentication, routing, media streaming |
| `services/upload.py` | Streaming file validation, ffprobe inspection, originals and upload events |
| `services/metadata.py` | Media facts and ordered lifecycle projection |
| `services/orchestrator.py` | Job creation, active-job guard, task coordination and lifecycle |
| `services/worker.py` | Transcoding API and command consumer |
| `services/thumbnail.py` | Thumbnail API and command consumer |
| `services/delivery.py` | Signed media catalogs and byte-range delivery |
| `common/processor.py` | SQL claims, worker leases, FFmpeg, attempts and retries |
| `common/bus.py` | Durable topology, confirmed publication, delayed retries and outbox relay |
| `migrations/` | Service-scoped Alembic schema history |
| `frontend/` | React dashboard, Vite, Nginx |
| `tests/` | Validation, jobs, idempotency, failures, recovery, contracts and E2E |

## Architecture and contracts

Read [the architecture diagram and ownership model](docs/architecture.md), [API documentation](docs/api.md), and [message contracts](docs/events.md).

Every service accesses only its own database. `infra/init.sql` grants each database user access only to its own schema. Delivery and gateway have no database. Shared Python modules provide contracts and utilities; no service queries another service's tables. Identifiers across service boundaries are logical references. Worker attempts and outputs have local foreign keys to worker work items.

## Run services independently

Use Python 3.12, MySQL 8, RabbitMQ, Redis and FFmpeg with `ffprobe` on PATH. Install requirements and provide these environment variables as needed:

```sh
SERVICE=worker
WORKER_DATABASE_URL=mysql+pymysql://worker:worker@localhost/worker
RABBITMQ_URL=amqp://platform:platform@localhost/
REDIS_URL=redis://localhost:6379/0
MEDIA_ROOT=/absolute/shared/media
INTERNAL_TOKEN=your-service-token
SIGNING_SECRET=your-signing-secret
python scripts/start.py
```

The example uses POSIX assignment syntax; set environment variables using `$env:NAME='value'` in PowerShell. Select `gateway`, `upload`, `metadata`, `orchestrator`, `worker`, `thumbnail`, or `delivery` through `SERVICE`. `PORT` defaults to 8000. The corresponding database variable is `{SERVICE}_DATABASE_URL` in uppercase. Configure HTTP destinations through `UPLOAD_URL`, `METADATA_URL`, `ORCHESTRATOR_URL`, `WORKER_URL`, `THUMBNAIL_URL`, and `DELIVERY_URL` when hostnames differ from Compose service names. All media participants must mount the same storage path.

For frontend development, run `npm ci && npm run dev` in `frontend`; Vite proxies `/api` to localhost:8000. Nginx does the equivalent in Docker. Use `PUBLIC_BASE_URL` for the gateway origin browsers can reach.

Apply or inspect a migration independently:

```sh
python -m alembic -x service=worker upgrade head
python -m alembic -x service=worker current
```

Repeat for upload, metadata, orchestrator and thumbnail. Gateway and delivery have no migrations. Startup migrations suit a single replica; for multiple replicas, run migrations once as a release step and launch `uvicorn services.worker:app --host 0.0.0.0 --port 8000` directly. One SQL claim per job prevents parallel processing of duplicates across replicas. `docker compose up -d --scale worker=2` works after schema initialization.

## Failure and recovery behavior

- Upload acceptance and its event commit in one database transaction. Job creation and both task commands do the same. An outbox relay retries publication after outages.
- Persistent messages, durable queues, publisher confirms and manual acknowledgements provide at-least-once delivery. Duplicate effects are prevented with inbox IDs, task state and unique constraints.
- Three processing attempts per task by default. Failed attempts retain error text and finish timestamps. Retries pass through service retry queues with a 10-second delay. Attempts and delay are configurable in `.env`.
- An expired SQL lease records an interrupted attempt as failed and allows recovery on redelivery. The lease exceeds the task's FFmpeg time budget by 60 seconds. No Redis lock is needed for correctness.
- Exhausted work is marked failed and a diagnostic command is durably published to `worker.dead` or `thumbnail.dead`. Malformed messages are rejected to the service's dead-letter queue. Infrastructure outages retry through delayed queues without consuming an encoding attempt.
- Orchestrator waits for both tasks to finish before releasing the active-video guard. Completed or failed task results cannot regress. Metadata projections compare event timestamps. Clicking **Retry processing** creates a new job after terminal failure; the old job and attempts remain queryable.
- Redis stores approximate per-task progress at resolution boundaries. Cache failures do not affect durable job state. Progress is deliberately coarse, not frame-by-frame encoder progress.
- Media links are HMAC-signed and expire after one hour. Only completed records can be delivered. The gateway proxies byte ranges for HTML video seeking. Catalog polling refreshes links.

Inspect logs with `docker compose logs -f worker thumbnail orchestrator`. Inspect dead queues through RabbitMQ management. Correct an underlying encoder/storage error, then request a new processing job; do not blindly replay terminal dead messages to the same job ID.

## Scope and deployment assumptions

This assignment uses a shared Docker volume and a fixed configurable resolution ladder (any subset of 360, 720, 1080). Lower-resolution sources are upscaled to the requested height; aspect ratio is preserved and width is rounded to an even number. Outputs are H.264/AAC MP4s; thumbnails use the first video frame so very short videos are supported. There is no HLS/DASH packaging, cancellation, deletion, or per-user tenant isolation in the assignment scope.

The default credentials and database grants are for local demonstration. A hosted deployment needs TLS, managed secrets and per-user authorization. A multi-host deployment needs shared network/object storage instead of a host-local Docker volume. Production operations also need database backups, outbox/inbox retention, media garbage collection, resource quotas, and monitoring. Crash-orphaned files and unsuccessful attempt directories are retained; they are never listed as completed outputs.

Stop without deleting data: `docker compose down`. Database, RabbitMQ and media data persist in named volumes. To change initialization credentials on an existing installation, migrate the database users explicitly; MySQL initialization scripts run only on a fresh data volume.
