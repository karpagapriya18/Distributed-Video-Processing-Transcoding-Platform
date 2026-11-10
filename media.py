import json
import subprocess
from pathlib import Path
from fastapi import HTTPException
from common.config import settings

EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm'}
MIME_TYPES = {'video/mp4', 'video/quicktime', 'video/x-matroska', 'video/webm', 'application/octet-stream'}


def validate_upload(filename: str, content_type: str, size: int):
    if Path(filename).suffix.lower() not in EXTENSIONS or content_type not in MIME_TYPES:
        raise HTTPException(415, 'Allowed video formats: MP4, MOV, MKV, WebM')
    if size > settings().max_upload_bytes:
        raise HTTPException(413, 'Video exceeds the upload size limit')
    if size == 0:
        raise HTTPException(422, 'Video is empty')


def probe(path: Path) -> dict:
    try:
        result = subprocess.run([settings().ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_format', '-show_streams',
                                 '-of', 'json', str(path)], capture_output=True, text=True, timeout=30, check=True)
        data = json.loads(result.stdout)
        containers = set(data['format']['format_name'].split(','))
        if not containers.intersection({'mov', 'mp4', 'matroska', 'webm'}):
            raise ValueError('Container is not permitted')
        video = next(s for s in data['streams'] if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic'))
        duration = float(data['format'].get('duration', video.get('duration', 0)))
        if duration <= 0 or video['width'] <= 0 or video['height'] <= 0:
            raise ValueError('Invalid video dimensions or duration')
        return {'format': data['format']['format_name'], 'duration': duration,
                'width': video['width'], 'height': video['height']}
    except (subprocess.SubprocessError, KeyError, StopIteration, ValueError) as exc:
        raise HTTPException(422, 'File does not contain a valid, decodable video stream') from exc


def run_ffmpeg(arguments: list[str], timeout: float):
    result = subprocess.run([settings().ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                            '-protocol_whitelist', 'file,pipe', *arguments],
                            capture_output=True, text=True, timeout=max(1, timeout))
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or f'FFmpeg exited with {result.returncode}')
