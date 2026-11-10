import os
import shutil
import subprocess
import pytest
from common.config import settings
from common.models import uid
from common.security import media_path
from services import thumbnail, worker


@pytest.mark.ffmpeg
def test_real_ffmpeg_generates_multiple_resolutions_and_thumbnail(monkeypatch):
    executable=os.getenv('TEST_FFMPEG') or shutil.which('ffmpeg')
    if not executable:
        pytest.skip('Set TEST_FFMPEG or install FFmpeg to run real encoder test')
    monkeypatch.setattr(settings(),'ffmpeg',executable)
    source=media_path('originals/fixture.mp4')
    source.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run([executable,'-y','-f','lavfi','-i','testsrc2=size=320x180:rate=10','-t','0.4','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True,capture_output=True,timeout=30)
    data={'video_id':uid(),'job_id':uid(),'path':'originals/fixture.mp4','resolutions':[360,720,1080]}
    outputs=worker.processor.generate(data,1)
    thumbs=thumbnail.processor.generate(data,1)
    assert [o['resolution'] for o in outputs]==[360,720,1080]
    assert all(media_path(o['path']).stat().st_size>100 for o in outputs)
    assert media_path(thumbs[0]['path']).read_bytes()[:2]==b'\xff\xd8'
    for output in outputs:
        # Decode a frame from every produced MP4; this also checks encoder output validity.
        result=subprocess.run([executable,'-v','error','-i',str(media_path(output['path'])),'-frames:v','1','-f','null','-'],capture_output=True,timeout=30)
        assert result.returncode==0, result.stderr
