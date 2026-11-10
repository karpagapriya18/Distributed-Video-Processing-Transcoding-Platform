
import asyncio
from typing import Literal
from uuid import UUID

import httpx
import redis.asyncio as redis

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from common.app import create_app
from common.config import settings
from common.security import authorized, headers
from common.contracts import ProcessRequest


# ============================================================
# API GATEWAY
# Distributed Video Processing & Transcoding Platform
# ============================================================

app = create_app("gateway")
auth = [Depends(authorized)]


# ============================================================
# SHARED UPSTREAM SERVICE COMMUNICATION
# ============================================================

async def upstream(base, path, method="GET", **kwargs):
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.request(
                method,
                f"{base}{path}",
                headers=headers(),
                **kwargs
            )

        if response.status_code >= 500:
            raise HTTPException(
                503,
                "A required service is temporarily unavailable"
            )

        if response.status_code >= 400:
            try:
                detail = response.json().get(
                    "detail", "Service request failed"
                )
            except (ValueError, AttributeError):
                detail = "Service request failed"

            raise HTTPException(response.status_code, detail)

        return response.json()

    except httpx.HTTPError as exc:
        raise HTTPException(
            503,
            "A required service is temporarily unavailable"
        ) from exc


# ============================================================
# 1. VIDEO UPLOAD MANAGEMENT
# ============================================================

@app.post(
    "/videos/upload",
    status_code=201,
    dependencies=auth,
    tags=["01 - Video Upload Management"],
    summary="Upload New Video",
    description="Upload a video with a title and validate upload size.",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file", "title"],
                        "properties": {
                            "file": {
                                "type": "string",
                                "format": "binary"
                            },
                            "title": {
                                "type": "string",
                                "maxLength": 200
                            }
                        }
                    }
                }
            }
        }
    }
)
async def upload(request: Request):
    limit = settings().max_upload_bytes + 1024 * 1024

    try:
        if int(request.headers.get("content-length", "0")) > limit:
            raise HTTPException(
                413, "Upload exceeds the request size limit"
            )
    except ValueError:
        raise HTTPException(400, "Invalid Content-Length")

    async def chunks():
        total = 0

        async for chunk in request.stream():
            total += len(chunk)

            if total > limit:
                raise HTTPException(
                    413, "Upload exceeds the request size limit"
                )

            yield chunk

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=10)
        ) as client:
            response = await client.post(
                f"{settings().upload_url}/videos/upload",
                content=chunks(),
                headers={
                    **headers(),
                    "Content-Type": request.headers.get(
                        "content-type", ""
                    )
                }
            )

    except httpx.HTTPError as exc:
        raise HTTPException(
            503, "Upload service unavailable"
        ) from exc

    if response.status_code >= 500:
        raise HTTPException(503, "Upload service unavailable")

    body = response.json()

    if response.status_code == 201 and isinstance(body, dict):
        body.pop("path", None)

    return JSONResponse(
        body,
        status_code=response.status_code
    )


# ============================================================
# 2. VIDEO MANAGEMENT
# ============================================================

@app.get(
    "/videos",
    dependencies=auth,
    tags=["02 - Video Management"],
    summary="List Uploaded Videos",
    description="Retrieve uploaded videos using pagination."
)
async def videos(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    rows = await upstream(
        settings().upload_url,
        f"/videos?limit={limit}&offset={offset}"
    )

    for row in rows:
        row.pop("path", None)

    return rows


@app.get(
    "/videos/{video_id}",
    dependencies=auth,
    tags=["02 - Video Management"],
    summary="Get Video Details",
    description="Retrieve video information and associated metadata."
)
async def video(video_id: UUID):
    row = await upstream(
        settings().upload_url,
        f"/videos/{video_id}"
    )

    row.pop("path", None)

    try:
        row["metadata"] = await upstream(
            settings().metadata_url,
            f"/videos/{video_id}"
        )
    except HTTPException as exc:
        if exc.status_code != 404:
            raise

        row["metadata"] = None

    row["processing"] = await upstream(
        settings().orchestrator_url,
        f"/videos/{video_id}/status"
    )

    return row


# ============================================================
# 3. VIDEO PROCESSING
# ============================================================

@app.get(
    "/videos/{video_id}/status",
    dependencies=auth,
    tags=["03 - Video Processing"],
    summary="Get Video Processing Status",
    description="Retrieve processing status and cached progress."
)
async def status(video_id: UUID):
    await upstream(
        settings().upload_url,
        f"/videos/{video_id}"
    )

    row = await upstream(
        settings().orchestrator_url,
        f"/videos/{video_id}/status"
    )

    if row.get("id") and row["status"] not in (
        "completed", "failed"
    ):
        cache = redis.Redis.from_url(
            settings().redis_url,
            socket_connect_timeout=0.2,
            socket_timeout=0.2
        )

        try:
            values = await cache.mget(
                f"progress:{row['id']}:transcode",
                f"progress:{row['id']}:thumbnail"
            )

            row["progress"] = max(
                row["progress"],
                min(
                    99,
                    sum(int(v or 0) for v in values) // 2
                )
            )

        except (redis.RedisError, ValueError):
            pass

        finally:
            await cache.aclose()

    return row


@app.post(
    "/videos/{video_id}/process",
    status_code=202,
    dependencies=auth,
    tags=["03 - Video Processing"],
    summary="Start Video Processing",
    description="Submit a video for asynchronous transcoding."
)
async def process(
    video_id: UUID,
    request: ProcessRequest
):
    return await upstream(
        settings().orchestrator_url,
        f"/videos/{video_id}/process",
        "POST",
        json=request.model_dump()
    )


# ============================================================
# 4. JOB MANAGEMENT
# ============================================================

@app.get(
    "/jobs",
    dependencies=auth,
    tags=["04 - Job Management"],
    summary="List Processing Jobs",
    description="Filter jobs by status or video ID."
)
async def list_jobs(
    status: Literal[
        "queued",
        "processing",
        "retrying",
        "completed",
        "failed"
    ] | None = None,
    video_id: UUID | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    params = {
        "limit": limit,
        "offset": offset
    }

    if status is not None:
        params["status"] = status

    if video_id is not None:
        params["video_id"] = str(video_id)

    return await upstream(
        settings().orchestrator_url,
        "/jobs",
        params=params
    )


# ============================================================
# 09 - JOB ANALYTICS
# Static job routes must come before /jobs/{job_id}.
# ============================================================

@app.get(
    "/jobs/statistics",
    dependencies=auth,
    tags=["09 - Job Analytics"],
    summary="Get Job Statistics",
    description="Retrieve job totals, status counts and success rate."
)
async def gateway_job_statistics():
    return await upstream(
        settings().orchestrator_url,
        "/jobs/statistics"
    )


@app.get(
    "/jobs/failed",
    dependencies=auth,
    tags=["09 - Job Analytics"],
    summary="List Failed Processing Jobs",
    description="Retrieve failed processing jobs with pagination."
)
async def gateway_failed_jobs(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    return await upstream(
        settings().orchestrator_url,
        "/jobs/failed",
        params={"limit": limit, "offset": offset}
    )


@app.get(
    "/jobs/{job_id}",
    dependencies=auth,
    tags=["04 - Job Management"],
    summary="Get Processing Job",
    description="Retrieve job information and worker attempts."
)
async def job(job_id: UUID):
    row = await upstream(
        settings().orchestrator_url,
        f"/jobs/{job_id}"
    )

    attempts = await asyncio.gather(
        upstream(
            settings().worker_url,
            f"/jobs/{job_id}/attempts"
        ),
        upstream(
            settings().thumbnail_url,
            f"/jobs/{job_id}/attempts"
        )
    )

    row["processing_attempts"] = {
        "transcode": attempts[0],
        "thumbnail": attempts[1]
    }

    return row


@app.get(
    "/videos/{video_id}/jobs",
    dependencies=auth,
    tags=["04 - Job Management"],
    summary="Get Jobs for Video",
    description="Retrieve all processing jobs for a video."
)
async def video_jobs(
    video_id: UUID,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    await upstream(
        settings().upload_url,
        f"/videos/{video_id}"
    )

    return await upstream(
        settings().orchestrator_url,
        "/jobs",
        params={
            "video_id": str(video_id),
            "limit": limit,
            "offset": offset
        }
    )


# ============================================================
# JOB PROCESSING ATTEMPT HISTORY
# ============================================================

@app.get(
    "/jobs/{job_id}/attempts",
    dependencies=auth,
    tags=["04 - Job Management"],
    summary="Get Job Processing Attempt History",
    description=(
        "Retrieve transcode and thumbnail processing "
        "attempts, including retry history and errors."
    )
)
async def job_attempt_history(job_id: UUID):
    await upstream(
        settings().orchestrator_url,
        f"/jobs/{job_id}"
    )

    transcode, thumbnail = await asyncio.gather(
        upstream(
            settings().worker_url,
            f"/jobs/{job_id}/attempts"
        ),
        upstream(
            settings().thumbnail_url,
            f"/jobs/{job_id}/attempts"
        )
    )

    return {
        "job_id": str(job_id),
        "transcode_attempts": transcode,
        "thumbnail_attempts": thumbnail
    }


# ============================================================
# 5. TRANSCODED VIDEO OUTPUTS
# ============================================================

@app.get(
    "/videos/{video_id}/outputs",
    dependencies=auth,
    tags=["05 - Transcoded Outputs"],
    summary="Get Video Outputs",
    description="Retrieve available transcoded video files."
)
async def outputs(video_id: UUID):
    await upstream(
        settings().upload_url,
        f"/videos/{video_id}"
    )

    return await upstream(
        settings().delivery_url,
        f"/videos/{video_id}/outputs"
    )


# ============================================================
# 6. THUMBNAIL MANAGEMENT
# ============================================================

@app.get(
    "/videos/{video_id}/thumbnails",
    dependencies=auth,
    tags=["06 - Thumbnail Management"],
    summary="Get Video Thumbnails",
    description="Retrieve generated thumbnails for a video."
)
async def thumbnails(video_id: UUID):
    await upstream(
        settings().upload_url,
        f"/videos/{video_id}"
    )

    return await upstream(
        settings().delivery_url,
        f"/videos/{video_id}/thumbnails"
    )


# ============================================================
# 7. MEDIA DELIVERY
# ============================================================

@app.get(
    "/media/{kind}/{item_id}",
    tags=["07 - Media Delivery"],
    summary="Stream Video or Thumbnail",
    description="Stream signed media with supported HTTP range headers."
)
async def media(
    kind: str,
    item_id: UUID,
    request: Request,
    expires: int,
    signature: str
):
    if kind not in ("outputs", "thumbnails"):
        raise HTTPException(404)

    client = httpx.AsyncClient(timeout=60)

    try:
        proxy_headers = {
            k: v
            for k, v in request.headers.items()
            if k in (
                "range",
                "if-range",
                "if-none-match",
                "if-modified-since"
            )
        }

        response = await client.send(
            client.build_request(
                "GET",
                (
                    f"{settings().delivery_url}"
                    f"/media/{kind}/{item_id}"
                ),
                params={
                    "expires": expires,
                    "signature": signature
                },
                headers=proxy_headers
            ),
            stream=True
        )

    except httpx.HTTPError as exc:
        await client.aclose()

        raise HTTPException(
            503,
            "Media delivery unavailable"
        ) from exc

    async def close():
        await response.aclose()
        await client.aclose()

    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers={
            k: v
            for k, v in response.headers.items()
            if k in (
                "content-type",
                "content-length",
                "content-range",
                "accept-ranges",
                "etag",
                "last-modified",
                "cache-control",
                "x-content-type-options"
            )
        },
        background=BackgroundTask(close)
    )


# ============================================================
# 8. OPERATIONS AND MONITORING
# ============================================================

@app.get(
    "/operations/summary",
    dependencies=auth,
    tags=["08 - Operations and Monitoring"],
    summary="Get Operations Summary",
    description="Retrieve overall processing statistics."
)
async def summary():
    return await upstream(
        settings().orchestrator_url,
        "/operations/summary"
    )


# ============================================================
# SWAGGER CATEGORY DISPLAY ORDER
# Keep static /jobs/statistics and /jobs/failed routes above
# /jobs/{job_id}; this only changes the documentation order.
# ============================================================

from fastapi.openapi.utils import get_openapi

SWAGGER_TAG_ORDER = [
    "operations",
    "01 - Video Upload Management",
    "02 - Video Management",
    "03 - Video Processing",
    "04 - Job Management",
    "05 - Transcoded Outputs",
    "06 - Thumbnail Management",
    "07 - Media Delivery",
    "08 - Operations and Monitoring",
    "09 - Job Analytics",
]


def custom_openapi():
    if app.openapi_schema is not None:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["tags"] = [{"name": tag} for tag in SWAGGER_TAG_ORDER]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
