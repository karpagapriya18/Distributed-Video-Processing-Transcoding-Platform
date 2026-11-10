# Messaging contract v1

All messages are persistent JSON in the durable topic exchange `video.events`, with a unique event UUID as the AMQP `message_id` and in the envelope. Every service declares the topology before it publishes, so subscribers can start later without losing their queue bindings.

```json
{
  "id":"d658e762-fef3-49e0-94a3-05dd4d73a805",
  "version":1,
  "type":"transcode.requested",
  "data":{
    "job_id":"22bc6155-2c52-43b2-8ccd-f4fb01f48174",
    "video_id":"6101d932-3053-4795-9d29-47ef7a958dc4",
    "path":"originals/6101d932-3053-4795-9d29-47ef7a958dc4.mp4",
    "resolutions":[360,720,1080]
  }
}
```

| Routing key | Producer → consumer | Required data |
| --- | --- | --- |
| `video.uploaded` | upload → metadata | video_id, title, size, format, duration, width, height |
| `transcode.requested` | orchestrator → worker | job_id, video_id, path, resolutions |
| `thumbnail.requested` | orchestrator → thumbnail | job_id, video_id, path, resolutions |
| `task.finished` | worker / thumbnail → orchestrator | job_id, task (`transcode` / `thumbnail`), status, attempts; optional error |
| `video.status` | orchestrator → metadata | video_id, status, changed_at (UTC ISO), optional error |

The historical name `task.finished` carries state transitions (`running`, `retrying`, `completed`, `failed`), not only terminal results. Each transition is committed alongside its source database change. Terminal task states are monotonic; inbox IDs suppress redelivery. Metadata compares `changed_at` to prevent old status events overwriting newer projections.

| Queue | Binding / behavior |
| --- | --- |
| `metadata` | video.uploaded, video.status |
| `orchestrator` | task.finished |
| `worker` | transcode.requested |
| `thumbnail` | thumbnail.requested |
| `{service}.retry` | Direct publication, 10-second per-message TTL, default-exchange dead-letter route to `{service}` |
| `{service}.dead` | Bound to `video.dead` with service routing key; also receives explicit exhausted-task diagnostics |

Main queues dead-letter rejected malformed messages to `video.dead`. Exhausted-task diagnostics are emitted via the outbox directly to the service's `.dead` queue. A retry outbox envelope uses type `worker.retry` or `thumbnail.retry` and retains the original command data. Transient callback failures preserve the original body and go through the same delayed retry queue.

Encoding attempt limits are SQL state, independent of message delivery counts. Busy duplicates do not consume attempts. A missing database or broker is treated as an infrastructure outage and retried; operators can inspect queue depth and service logs. Unknown contract versions and invalid commands are rejected. Broker messages are trusted service-origin traffic inside the private deployment network.

Publisher acknowledgements and consumer acknowledgements cover different steps, so both are enabled; see [RabbitMQ publisher confirms and acknowledgements](https://www.rabbitmq.com/docs/confirms). Upload requests use FastAPI's multipart support; see [FastAPI file uploads](https://fastapi.tiangolo.com/tutorial/request-files/).
