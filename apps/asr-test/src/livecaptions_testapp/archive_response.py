import re

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from .archive import AudioArchive, wav_header


def archive_response(archive: AudioArchive, request: Request):
    size = len(wav_header(archive.frames)) + archive.frames * 2
    begin, finish, status = 0, size, 200
    headers = {"Accept-Ranges": "bytes"}
    if value := request.headers.get("range"):
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
        if not match or not any(match.groups()):
            raise HTTPException(
                416, "Invalid byte range", headers={"Content-Range": f"bytes */{size}"}
            )
        left, right = match.groups()
        if left:
            begin = int(left)
            finish = min(size, int(right) + 1) if right else size
        else:
            begin = max(0, size - int(right))
        if begin >= finish or begin >= size:
            raise HTTPException(
                416, "Range outside audio", headers={"Content-Range": f"bytes */{size}"}
            )
        status = 206
        headers["Content-Range"] = f"bytes {begin}-{finish - 1}/{size}"
    headers["Content-Length"] = str(finish - begin)
    return StreamingResponse(
        archive.iter_wav(begin, finish), status_code=status, media_type="audio/wav", headers=headers
    )
