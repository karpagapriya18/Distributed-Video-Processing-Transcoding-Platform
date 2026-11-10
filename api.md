# Public API

Base URL: `http://localhost:8000`. All `/videos` and `/jobs` endpoints require `Authorization: Bearer <API_KEY>`. UUID path parameters are validated. Errors use FastAPI's `{"detail": ...}` envelope. Interactive documentation is at `/docs`, JSON at `/openapi.json`. A generated [OpenAPI snapshot](openapi.json) is included; regenerate it with `python scripts/export_openapi.py`.

| Method and path | Request | Success |
| --- | --- | --- |
| `POST /videos/upload` | Multipart `title` (1–200 nonblank characters), `file` | `201` original record |
| `GET /videos` | `limit` 1–100 (default 50), `offset` ≥ 0 | `200` newest-first original records |
| `GET /videos/{video_id}` | — | `200` original + `metadata` + `processing` |
| `GET /videos/{video_id}/status` | — | `200` latest job or `uploaded` state |
| `POST /videos/{video_id}/process` | JSON `{"resolutions":[360,720,1080]}` | `202` queued job |
| `GET /jobs/{job_id}` | — | `200` job + both services' attempts |
| `GET /videos/{video_id}/outputs` | — | `200` completed outputs + signed URLs |
| `GET /videos/{video_id}/thumbnails` | — | `200` completed thumbnails + signed URLs |
| `GET /media/{kind}/{item_id}` | `expires`, `signature`; optional Range header | `200` media or `206` range |
| `GET /health` | — | `200` process readiness |

Typical errors: `401` invalid key, `403` invalid/expired media signature, `404` unknown video/job/media, `409` already active processing, `413` upload too large, `415` forbidden extension/MIME, `422` invalid request or video, `503` unavailable downstream service. Empty catalogs return `[]`. No job yet returns `{"video_id":"...","status":"uploaded","progress":0,"attempts":0}`. Before its upload event is projected, the detail endpoint returns `metadata: null`.

```sh
curl -H 'Authorization: Bearer local-demo-key-change-me' \
  -F 'title=Launch film' -F 'file=@sample.mp4;type=video/mp4' \
  http://localhost:8000/videos/upload

curl -H 'Authorization: Bearer local-demo-key-change-me' \
  -H 'Content-Type: application/json' -d '{"resolutions":[360,720,1080]}' \
  http://localhost:8000/videos/VIDEO_UUID/process

curl -H 'Authorization: Bearer local-demo-key-change-me' \
  http://localhost:8000/videos/VIDEO_UUID/status
```

On Windows PowerShell, use `curl.exe` when the `curl` alias maps to `Invoke-WebRequest`.

Example job response (timestamps omitted here):

```json
{
  "id":"22bc6155-2c52-43b2-8ccd-f4fb01f48174",
  "video_id":"6101d932-3053-4795-9d29-47ef7a958dc4",
  "active_video_id":null,
  "resolutions":[360,720,1080],
  "status":"completed",
  "transcode_status":"completed",
  "thumbnail_status":"completed",
  "progress":100,
  "attempts":1,
  "error":null,
  "processing_attempts":{"transcode":[],"thumbnail":[]}
}
```

`attempts` is the maximum attempt count across both tasks. `processing_attempts` contains the actual recorded attempt objects, including number, timestamps, status, and error. The example arrays are abbreviated. Job state is `queued`, `processing`, `retrying`, `completed`, or `failed`. Individual tasks also report `running`. `progress` is a percentage; it advances at task/resolution boundaries. Retry information and previous errors stay available while processing. A successful job clears its aggregate error; historical failed attempts retain their errors.

Process requests allow any nonempty subset of 360, 720, and 1080. Duplicate and unsupported resolutions are rejected. Omit `resolutions` in an empty JSON object for all three. A new explicit request after a terminal job creates a new job UUID and retains earlier outputs and attempts. Catalogs contain completed outputs from all jobs, newest first, with `job_id` to identify their source.

Media URLs are bearer capabilities valid for one hour and require no separate API key. Do not log or share them unintentionally. `/media/outputs/...` serves MP4, `/media/thumbnails/...` serves JPEG. Catalog responses never expose filesystem paths. Byte-range headers and responses are proxied by the gateway, so browser seeking works.

## Internal APIs

Each service exposes `/docs` on its internal port and requires `X-Internal-Token` for catalog and control endpoints. The Compose network keeps these ports private. Public incoming credentials are never used as internal service credentials.

- Upload: `POST /videos/upload`, `GET /videos`, `GET /videos/{id}` (includes private original path).
- Metadata: `GET /videos/{id}`.
- Orchestrator: `POST /videos/{id}/process`, `GET /videos/{id}/status`, `GET /jobs/{id}`.
- Worker: `GET /videos/{id}/outputs`, `GET /outputs/{id}`, `GET /jobs/{id}/attempts`.
- Thumbnail: `GET /videos/{id}/thumbnails`, `GET /thumbnails/{id}`, `GET /jobs/{id}/attempts`.
- Delivery: `GET /videos/{id}/outputs`, `GET /videos/{id}/thumbnails`, signed `/media/{kind}/{id}`.

Health endpoints are unauthenticated. Services with databases check SQL connectivity and broker connection state. Gateway and delivery report process liveness; upstream availability is reflected by `503` responses. Redis is optional for progress and does not block startup.
