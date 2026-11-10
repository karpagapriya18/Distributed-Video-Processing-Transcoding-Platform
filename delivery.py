from uuid import UUID
import httpx
from fastapi import Depends, HTTPException, Query
from fastapi.responses import FileResponse
from common.app import create_app
from common.config import settings
from common.security import headers, internal, media_path, signed_url, verify_signature

app = create_app('delivery')


async def fetch(url):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers())
        if response.status_code == 404:
            raise HTTPException(404, 'Media record not found')
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(503, 'Media catalog unavailable') from exc


@app.get('/videos/{video_id}/{kind}', dependencies=[Depends(internal)])
async def catalog(video_id: UUID, kind: str):
    if kind not in ('outputs', 'thumbnails'):
        raise HTTPException(404)
    base = settings().worker_url if kind == 'outputs' else settings().thumbnail_url
    rows = await fetch(f'{base}/videos/{video_id}/{kind}')
    return [{**{k: v for k, v in row.items() if k != 'path'}, 'url': signed_url(kind, row['id'])} for row in rows]


@app.get('/media/{kind}/{item_id}')
async def media(kind: str, item_id: UUID, expires: int = Query(...), signature: str = Query(...)):
    if kind not in ('outputs', 'thumbnails'):
        raise HTTPException(404)
    verify_signature(kind, str(item_id), expires, signature)
    base = settings().worker_url if kind == 'outputs' else settings().thumbnail_url
    row = await fetch(f'{base}/{kind}/{item_id}')
    path = media_path(row['path'])
    if row['status'] != 'completed' or not path.is_file():
        raise HTTPException(404, 'Media file is unavailable')
    return FileResponse(path, media_type='video/mp4' if kind == 'outputs' else 'image/jpeg',
                        headers={'Cache-Control': 'private, max-age=300', 'X-Content-Type-Options': 'nosniff'})
