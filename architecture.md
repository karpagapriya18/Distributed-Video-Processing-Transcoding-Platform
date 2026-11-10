# Architecture

Solid arrows are synchronous HTTP or filesystem access. Dashed arrows are asynchronous RabbitMQ messages. Each database shown belongs exclusively to its service.

```mermaid
flowchart TB
  browser[React + TypeScript dashboard] -->|HTTP /api + signed media| gateway[API Gateway]
  gateway --> upload[Upload service]
  gateway --> metadata[Metadata service]
  gateway --> orchestrator[Transcoding orchestrator]
  gateway -->|catalog + byte ranges| delivery[Media delivery]
  gateway -->|attempts| worker[Transcoding worker]
  gateway -->|attempts| thumb[Thumbnail service]
  orchestrator -->|validate original reference| upload
  delivery -->|output catalog| worker
  delivery -->|thumbnail catalog| thumb
  upload --> udb[(upload DB: videos + outbox)]
  metadata --> mdb[(metadata DB: video_metadata + inbox)]
  orchestrator --> odb[(orchestrator DB: jobs + inbox + outbox)]
  worker --> wdb[(worker DB: work_items + attempts + outputs + outbox)]
  thumb --> tdb[(thumbnail DB: work_items + attempts + thumbnails + outbox)]
  upload -. video.uploaded .-> mq{{RabbitMQ}}
  mq -. video.uploaded / video.status .-> metadata
  orchestrator -. transcode.requested / thumbnail.requested .-> mq
  mq -. transcode.requested .-> worker
  mq -. thumbnail.requested .-> thumb
  worker -. task.finished / retry / dead .-> mq
  thumb -. task.finished / retry / dead .-> mq
  mq -. task.finished .-> orchestrator
  orchestrator -. video.status .-> mq
  mq -. delayed retry .-> worker
  mq -. delayed retry .-> thumb
  mq -. exhausted or malformed .-> dlq[Service dead-letter queues]
  upload --> storage[(Shared media volume)]
  worker --> storage
  thumb --> storage
  delivery -->|read only| storage
  worker --> cache[(Redis progress cache)]
  thumb --> cache
  gateway --> cache
```

## Boundaries

| Service | Owns | External dependencies |
| --- | --- | --- |
| Gateway | No tables | HTTP services, optional Redis |
| Upload | `videos`, `outbox` | MySQL, shared storage, ffprobe, RabbitMQ |
| Metadata | `video_metadata`, `inbox` | MySQL, RabbitMQ |
| Orchestrator | `processing_jobs`, `inbox`, `outbox` | MySQL, upload HTTP, RabbitMQ |
| Worker | `work_items`, `processing_attempts`, `transcoding_outputs`, `outbox` | MySQL, RabbitMQ, FFmpeg, storage, optional Redis |
| Thumbnail | `work_items`, `processing_attempts`, `video_thumbnails`, `outbox` | MySQL, RabbitMQ, FFmpeg, storage, optional Redis |
| Delivery | No tables | Worker / thumbnail HTTP, read-only storage |

Every entity has a UUID and UTC timestamps. Cross-service video and job IDs are logical relationships, not cross-database foreign keys. Attempts and media records reference work items inside the owning schema. The two services' `processing_attempts` tables belong to different databases.

## Lifecycle

1. Gateway authenticates and streams a bounded multipart request to upload. Upload checks extension, MIME, byte count and actual video facts with ffprobe. It atomically renames the original and commits its record with an outbox event.
2. Metadata consumes the event idempotently. Its absence briefly after upload is normal eventual consistency; the original can already be processed.
3. Process API creates an active job and two outbox commands in a transaction. A nullable unique active-video field enforces one active job per video. It returns immediately with `202`.
4. Each consumer claims a SQL work item, increments its durable attempt and sets a lease. Processing runs outside the transaction. A duplicate terminal command does nothing; an in-flight duplicate is delayed.
5. Transcoding generates requested MP4s sequentially inside its time budget. Thumbnail generation runs independently. Files are written under a job and attempt directory and atomically renamed before being cataloged.
6. Successful media records, attempt completion and the result outbox event commit together. Retryable encoding failures commit a retry command and error; exhausted attempts commit terminal failure and a dead-letter diagnostic.
7. Orchestrator consumes results, updates the job and emits the aggregate lifecycle to metadata. Both tasks must become terminal before a subsequent job can start. Delivery retrieves completed catalog records over HTTP and issues signed URLs.

## Delivery semantics

Database commits and broker acknowledgements cannot be one distributed transaction. The outbox closes the publish-loss gap: publish, wait for a broker confirmation, then mark sent. A crash between those steps republishes the event, so all effects must tolerate duplicate messages. Inbox uniqueness protects event consumers; work item leases and output uniqueness protect processors.

Broker retry queues use TTL plus dead-letter routing back to the main queue. Transient errors preserve the original envelope and delay redelivery. Task failures create a new retry envelope, while the database enforces the attempt budget and the earliest next attempt time. Work surviving a container crash resumes when the broker redelivers and the old lease expires. Completed outputs are committed as a set per task; partial resolution files are not published as successful records.

The outbox poll interval is one second. UI polling is three seconds. Redis progress is a disposable projection. The durable job endpoint remains usable if Redis is unavailable.
