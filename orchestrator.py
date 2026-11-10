
from uuid import UUID
from typing import Literal

import httpx

from fastapi import Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from common.app import create_app
from common.config import settings
from common.contracts import ProcessRequest
from common.db import Database, emit
from common.models import Inbox, Job, now, serialize
from common.security import headers, internal


# ============================================================
# DISTRIBUTED VIDEO PROCESSING PLATFORM
# TRANSCODING ORCHESTRATOR SERVICE
# ============================================================

db = Database("orchestrator")


# ============================================================
# 1. JOB CREATION
# ============================================================

def create_job(video: dict, request: ProcessRequest):
    try:
        with db.transaction() as session:
            active = session.scalar(
                select(Job).where(
                    Job.active_video_id == video["id"]
                )
            )

            if active:
                raise HTTPException(
                    409,
                    f"Video already has active job {active.id}"
                )

            job = Job(
                video_id=video["id"],
                active_video_id=video["id"],
                resolutions=request.resolutions
            )

            session.add(job)
            session.flush()

            data = {
                "job_id": job.id,
                "video_id": video["id"],
                "path": video["path"],
                "resolutions": request.resolutions
            }

            emit(session, "transcode.requested", data)
            emit(session, "thumbnail.requested", data)

            emit(
                session,
                "video.status",
                {
                    "video_id": job.video_id,
                    "status": "queued",
                    "changed_at": now().isoformat()
                }
            )

            return serialize(job)

    except IntegrityError as exc:
        raise HTTPException(
            409,
            "Video already has an active job"
        ) from exc


# ============================================================
# 2. RABBITMQ EVENT HANDLER
# ============================================================

def handle(envelope):
    if (
        envelope.get("version") != 1
        or envelope["type"] != "task.finished"
    ):
        raise ValueError("Unsupported event")

    data = envelope["data"]

    if (
        data["task"] not in ("transcode", "thumbnail")
        or data["status"] not in (
            "completed", "failed", "retrying", "running"
        )
    ):
        raise ValueError("Invalid task state")

    with db.transaction() as session:
        existing = session.scalar(
            select(Inbox).where(
                Inbox.event_id == envelope["id"]
            )
        )

        if existing:
            return

        job = session.scalar(
            select(Job)
            .where(Job.id == data["job_id"])
            .with_for_update()
        )

        if not job:
            raise RuntimeError("Job not available yet")

        session.add(Inbox(event_id=envelope["id"]))

        field = f"{data['task']}_status"

        # Terminal states cannot regress.
        if getattr(job, field) in ("completed", "failed"):
            return

        setattr(job, field, data["status"])

        job.attempts = max(
            job.attempts,
            data["attempts"]
        )

        if data.get("error"):
            job.error = data["error"]

        states = (
            job.transcode_status,
            job.thumbnail_status
        )

        job.progress = (
            sum(s == "completed" for s in states) * 50
        )

        if all(
            s in ("completed", "failed")
            for s in states
        ):
            job.status = (
                "failed"
                if "failed" in states
                else "completed"
            )

            job.active_video_id = None

            if job.status == "completed":
                job.error = None
                job.progress = 100

        else:
            job.status = (
                "retrying"
                if "retrying" in states
                else "processing"
            )

        job.updated_at = now()

        emit(
            session,
            "video.status",
            {
                "video_id": job.video_id,
                "status": job.status,
                "error": job.error,
                "changed_at": job.updated_at.isoformat()
            }
        )


# ============================================================
# 3. FASTAPI APPLICATION
# ============================================================

app = create_app("orchestrator", db, handle)

auth = [Depends(internal)]

JobStatus = Literal[
    "queued",
    "processing",
    "retrying",
    "completed",
    "failed"
]


# ============================================================
# 4. START VIDEO PROCESSING
# ============================================================

@app.post(
    "/videos/{video_id}/process",
    status_code=202,
    dependencies=auth,
    tags=["Video Processing"],
    summary="Start Video Processing"
)
def process(
    video_id: UUID,
    request: ProcessRequest
):
    try:
        response = httpx.get(
            f"{settings().upload_url}/videos/{video_id}",
            headers=headers(),
            timeout=10
        )

        if response.status_code == 404:
            raise HTTPException(
                404,
                "Video not found"
            )

        response.raise_for_status()

    except httpx.HTTPError as exc:
        raise HTTPException(
            503,
            "Upload service unavailable"
        ) from exc

    return create_job(
        response.json(),
        request
    )


# ============================================================
# 5. JOB STATISTICS — NEW API
# ============================================================

@app.get(
    "/jobs/statistics",
    dependencies=auth,
    tags=["Job Analytics"],
    summary="Get Job Statistics",
    description="Retrieve job counts and processing success rate."
)
def job_statistics():
    with db.transaction() as session:
        rows = session.execute(
            select(
                Job.status,
                func.count()
            ).group_by(Job.status)
        ).all()

    counts = dict(rows)

    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)

    terminal = completed + failed

    statuses = (
        "queued",
        "processing",
        "retrying",
        "completed",
        "failed"
    )

    by_status = {
        state: counts.get(state, 0)
        for state in statuses
    }

    return {
        "total_jobs": sum(counts.values()),
        "completed_jobs": completed,
        "failed_jobs": failed,
        "active_jobs": sum(
            counts.get(state, 0)
            for state in (
                "queued",
                "processing",
                "retrying"
            )
        ),
        "success_rate": (
            round(
                completed / terminal * 100,
                2
            )
            if terminal
            else None
        ),
        "by_status": by_status
    }


# ============================================================
# 6. FAILED JOBS — NEW API
# ============================================================

@app.get(
    "/jobs/failed",
    dependencies=auth,
    tags=["Job Analytics"],
    summary="List Failed Jobs",
    description="Retrieve failed processing jobs with pagination."
)
def failed_jobs(
    limit: int = Query(
        25,
        ge=1,
        le=100
    ),
    offset: int = Query(
        0,
        ge=0
    )
):
    with db.transaction() as session:
        condition = Job.status == "failed"

        total = session.scalar(
            select(func.count())
            .select_from(Job)
            .where(condition)
        )

        rows = session.scalars(
            select(Job)
            .where(condition)
            .order_by(
                Job.created_at.desc(),
                Job.id.desc()
            )
            .limit(limit)
            .offset(offset)
        ).all()

        return {
            "items": [
                serialize(row)
                for row in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset
        }


# ============================================================
# 7. GET JOB DETAILS
# ============================================================

@app.get(
    "/jobs/{job_id}",
    dependencies=auth,
    tags=["Job Management"],
    summary="Get Processing Job"
)
def get_job(job_id: UUID):
    with db.transaction() as session:
        job = session.get(
            Job,
            str(job_id)
        )

        if not job:
            raise HTTPException(
                404,
                "Job not found"
            )

        return serialize(job)


# ============================================================
# 8. VIDEO PROCESSING STATUS
# ============================================================

@app.get(
    "/videos/{video_id}/status",
    dependencies=auth,
    tags=["Video Processing"],
    summary="Get Video Processing Status"
)
def status(video_id: UUID):
    with db.transaction() as session:
        job = session.scalar(
            select(Job)
            .where(
                Job.video_id == str(video_id)
            )
            .order_by(
                Job.created_at.desc()
            )
            .limit(1)
        )

        if job:
            return serialize(job)

        return {
            "video_id": str(video_id),
            "status": "uploaded",
            "progress": 0,
            "attempts": 0
        }


# ============================================================
# 9. LIST PROCESSING JOBS
# ============================================================

@app.get(
    "/jobs",
    dependencies=auth,
    tags=["Job Management"],
    summary="List Processing Jobs"
)
def list_jobs(
    status: JobStatus | None = None,
    video_id: UUID | None = None,
    limit: int = Query(
        25,
        ge=1,
        le=100
    ),
    offset: int = Query(
        0,
        ge=0
    )
):
    filters = []

    if status is not None:
        filters.append(
            Job.status == status
        )

    if video_id is not None:
        filters.append(
            Job.video_id == str(video_id)
        )

    with db.transaction() as session:
        total = session.scalar(
            select(func.count())
            .select_from(Job)
            .where(*filters)
        )

        rows = session.scalars(
            select(Job)
            .where(*filters)
            .order_by(
                Job.created_at.desc(),
                Job.id.desc()
            )
            .limit(limit)
            .offset(offset)
        ).all()

        return {
            "items": [
                serialize(row)
                for row in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset
        }


# ============================================================
# 10. OPERATIONS SUMMARY
# ============================================================

@app.get(
    "/operations/summary",
    dependencies=auth,
    tags=["Operations"],
    summary="Get Operations Summary"
)
def summary():
    with db.transaction() as session:
        counts = dict(
            session.execute(
                select(
                    Job.status,
                    func.count()
                ).group_by(Job.status)
            ).all()
        )

    by_status = {
        state: counts.get(state, 0)
        for state in (
            "queued",
            "processing",
            "retrying",
            "completed",
            "failed"
        )
    }

    terminal = (
        by_status["completed"]
        + by_status["failed"]
    )

    return {
        "total_jobs": sum(counts.values()),
        "by_status": by_status,
        "active_jobs": sum(
            by_status[state]
            for state in (
                "queued",
                "processing",
                "retrying"
            )
        ),
        "success_rate": (
            by_status["completed"] / terminal
            if terminal
            else None
        )
    }
