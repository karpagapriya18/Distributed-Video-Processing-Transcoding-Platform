# Backend upgrade — 4 November 2026

## Changes
- GET /jobs: authenticated search by status/video_id, bounded pagination, stable newest-first ordering, total count.
- GET /videos/{video_id}/jobs: authenticated processing history; returns 404 for an unknown video.
- GET /operations/summary: total jobs, status counts, active count and success rate among terminal jobs (null when none exist).
- Unexpected consumer failures now receive at most five delayed retries before rejection to the existing service dead-letter queue. Malformed messages are rejected immediately. Confirmed retry publication precedes acknowledgement; failed publication requeues the original message. Retry messages preserve message and correlation IDs.
- This transport retry limit is separate from MAX_ATTEMPTS for FFmpeg failures. Broker publish outages still requeue safely rather than losing messages. Dead-lettered unexpected failures do not automatically reconcile job status; inspect the service logs and dead queue before recovery.

No database migration, frontend change, or new dependency is required. Existing /jobs/{job_id} continues to expose processing attempts. No claims are made of JWT roles, per-user video ownership, cancellation, or production HA.

## Install on Windows
1. Extract this ZIP to its own folder, outside your project.
2. Run the included install-backend.ps1 from PowerShell, passing your project directory. The installer checks original file hashes, rejects conflicting local edits, and backs up every file it replaces. It does not change .env, frontend, Compose, database data, or media.
3. Rebuild the seven backend containers from the project directory:

```powershell
docker compose up -d --build upload metadata orchestrator worker thumbnail delivery gateway
docker compose ps -a
```

4. Visit http://localhost:8000/docs. The new routes appear under job management and operations. Supply the Authorization header value `Bearer YOUR_LOCAL_API_KEY` when trying protected routes.
5. Build and run the test container:

```powershell
docker compose --profile test build tests
docker compose --profile test run --rm --no-deps tests
```

The default retry count is five. To override in Docker, add `CONSUMER_MAX_RETRIES: ${CONSUMER_MAX_RETRIES:-5}` to the existing shared x-environment mapping in compose.yaml, set it in .env, and recreate the backend containers. Merely adding it to .env does not forward it into containers.

## Verification for this update
56 tests passed; 2 live-stack tests were deselected. Tests cover the existing backend, real FFmpeg output, new filters/pagination/authentication/statistics, retry exhaustion, invalid retry headers and message-ID preservation. One existing Starlette test-client deprecation warning occurred. Local tests use isolated SQLite databases and mocked broker interactions. Live MySQL/RabbitMQ E2E testing must be run on your Docker setup after installation. Earlier verification reports describe earlier versions, not this update.

## Rollback
The installer prints its timestamped backup directory. Restore the backed-up files into the project and rebuild the same backend services. Remove tests/test_job_management.py if it was newly added. No database rollback is necessary.
