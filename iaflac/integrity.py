"""V2: prove a file decodes cleanly without keeping it, by streaming the bytes into `flac -t`.

`flac -t` checks every frame's CRC and, when STREAMINFO carries one, the MD5 of the decoded audio.
We hash the same bytes on the way past and compare with the archive's own manifest, so both the
transfer and the audio are covered.
"""

from __future__ import annotations

import hashlib
import subprocess
import threading
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Optional

from . import binaries
from .events import Cancelled, CancelCheck
from .ia_client import IAClient, IAError
from .model import FileInfo


@dataclass
class IntegrityResult:
    ok: bool
    method: str
    bytes_read: int
    md5_ok: Optional[bool] = None        # matches IA's manifest (None: unknown or incomplete read)
    sha1_ok: Optional[bool] = None
    audio_md5_checked: Optional[bool] = None   # STREAMINFO had an MD5, so the decoder compared it to the audio
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def decoder_command() -> tuple:
    flac = binaries.flac()
    if flac:
        return "flac -t", [flac, "-t", "-s", "-"]
    ffmpeg = binaries.ffmpeg()
    if ffmpeg:
        return "ffmpeg", [ffmpeg, "-nostdin", "-v", "error", "-i", "pipe:0", "-f", "null", "-"]
    return "", []


def test_stream(chunks: Iterable[bytes], *, expected_size: Optional[int] = None, expected_md5: str = "",
                expected_sha1: str = "", audio_md5_set: Optional[bool] = None,
                on_bytes: Optional[Callable[[int], None]] = None, cancel: Optional[CancelCheck] = None) -> IntegrityResult:
    """Feed `chunks` to the decoder while hashing them. `on_bytes(total so far)` is called per chunk; when `cancel()`
    turns true the decoder is stopped and Cancelled is raised."""
    method, cmd = decoder_command()
    if not cmd:
        return IntegrityResult(False, "none", 0, message="neither flac nor ffmpeg is installed")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **binaries.NO_WINDOW)
    captured: list = []
    reader = threading.Thread(target=lambda: captured.append(proc.stderr.read()), daemon=True)
    reader.start()                       # drain stderr so a chatty decoder can never block on a full pipe
    md5, sha1, total = hashlib.md5(), hashlib.sha1(), 0
    decoder_gone, transfer_error, cancelled = False, "", False
    try:
        for chunk in chunks:
            if cancel is not None and cancel():
                cancelled = True
                break
            total += len(chunk)
            if on_bytes is not None:
                on_bytes(total)
            md5.update(chunk)
            sha1.update(chunk)
            if not decoder_gone:
                try:
                    proc.stdin.write(chunk)
                except (BrokenPipeError, OSError):
                    decoder_gone = True  # decoder already gave up; keep counting so the size is honest
    except Exception as exc:             # connection dropped mid-transfer
        transfer_error = f"transfer failed after {total} bytes ({type(exc).__name__})"
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    if transfer_error or cancelled:
        proc.kill()
    rc = proc.wait()
    reader.join(timeout=5)
    proc.stderr.close()
    if cancelled:
        raise Cancelled()
    stderr = (captured[0] if captured else b"").decode("utf-8", "replace").strip()

    complete = not transfer_error and not decoder_gone
    md5_ok = md5.hexdigest() == expected_md5.lower() if complete and expected_md5 else None
    sha1_ok = sha1.hexdigest() == expected_sha1.lower() if complete and expected_sha1 else None
    problems = []
    if transfer_error:
        problems.append(transfer_error)
    elif rc != 0 or decoder_gone:
        detail = next((ln.strip() for ln in stderr.splitlines() if ln.strip()), "no message")
        problems.append(f"{method} failed (exit {rc}): {detail}")
    if expected_size is not None and complete and total != expected_size:
        problems.append(f"received {total} bytes but IA lists {expected_size}")
    if md5_ok is False:
        problems.append("md5 differs from IA's manifest")
    if sha1_ok is False:
        problems.append("sha1 differs from IA's manifest")
    if problems:
        message = "; ".join(problems)
    else:
        message = "audio MD5 verified" if audio_md5_set else "frame CRCs verified (file carries no audio MD5)"
    return IntegrityResult(ok=not problems, method=method, bytes_read=total, md5_ok=md5_ok, sha1_ok=sha1_ok,
                           audio_md5_checked=audio_md5_set if not problems else None, message=message)


def test_file(client: IAClient, identifier: str, f: FileInfo, audio_md5_set: Optional[bool] = None,
              on_bytes: Optional[Callable[[int], None]] = None, cancel: Optional[CancelCheck] = None) -> IntegrityResult:
    try:
        resp = client.open_stream(identifier, f.name)
    except IAError as exc:
        return IntegrityResult(False, "none", 0, message=f"could not download: {exc}")
    try:
        result = test_stream(resp.iter_content(1 << 20), expected_size=f.size or None,
                             expected_md5=f.md5, expected_sha1=f.sha1, audio_md5_set=audio_md5_set,
                             on_bytes=on_bytes, cancel=cancel)
    finally:
        resp.close()
    client.bytes_read += result.bytes_read
    return result
