"""以真實 ffprobe／ffmpeg 驗證 MP4（唯讀、fail-closed）。

由 TRISYNORA `tools/pointflux_media_probe.py` 的通用部分遷入；不含 H3 或 PointFlux 專屬邏輯。
時基（fps／影格數）一律由媒體本身讀出，不信任呼叫端宣告。
通過驗證只代表「容器與 H.264 影格可被解碼」，不代表畫面品質、來源真實性或任何核准。

- 外部工具缺失或逾時 → `MediaProbeUnavailable`（NOT VERIFIED，呼叫端不得寫入證據）。
- 媒體不符政策或解碼失敗 → `MediaProbeRejected`（同為 `ContractError`，被拒絕）。
"""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .validator import ContractError

MAX_VIDEO_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_WIDTH = 1920
MAX_HEIGHT = 1080
MAX_SECONDS = 60
PROBE_TIMEOUT = 25
DECODE_TIMEOUT = 90


class MediaProbeRejected(ContractError):
    """媒體不符合政策或無法解碼。"""


class MediaProbeUnavailable(RuntimeError):
    """ffprobe／ffmpeg 不可用；結果為 NOT VERIFIED，絕不可視為通過。"""


def _run(binary: str, args: list[str], timeout: int) -> str:
    if shutil.which(binary) is None:
        raise MediaProbeUnavailable(f"{binary} is not installed or on PATH")
    try:
        result = subprocess.run([binary, *args], shell=False, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise MediaProbeUnavailable(f"{binary} timed out") from exc
    except OSError as exc:
        raise MediaProbeUnavailable(f"{binary} unavailable") from exc
    if len(result.stdout) > MAX_OUTPUT_BYTES or len(result.stderr) > MAX_OUTPUT_BYTES:
        raise MediaProbeRejected(f"{binary} emitted excessive output")
    if result.returncode != 0:
        raise MediaProbeRejected(f"{binary} reported media error; exit={result.returncode}")
    return result.stdout


def _fps(value: object) -> Fraction:
    if not isinstance(value, str):
        raise MediaProbeRejected("ffprobe frame rate unavailable")
    try:
        rate = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise MediaProbeRejected("ffprobe frame rate invalid") from exc
    if rate <= 0:
        raise MediaProbeRejected("ffprobe frame rate must be positive")
    return rate


def parse_probe(info: object) -> dict[str, Any]:
    """解析不可信的 ffprobe JSON，僅接受單一 H.264/yuv420p 影像串流的 MP4。"""
    if not isinstance(info, dict):
        raise MediaProbeRejected("ffprobe response must be an object")
    fmt, streams = info.get("format"), info.get("streams")
    if not isinstance(fmt, dict) or not isinstance(streams, list):
        raise MediaProbeRejected("ffprobe response is missing format or streams")
    if "mp4" not in str(fmt.get("format_name", "")).split(","):
        raise MediaProbeRejected("media container is not MP4")
    if len(streams) != 1 or not isinstance(streams[0], dict):
        raise MediaProbeRejected("pilot requires exactly one video stream and no others")
    stream = streams[0]
    if stream.get("codec_type") != "video" or stream.get("codec_name") != "h264":
        raise MediaProbeRejected("pilot requires H.264 video")
    if stream.get("pix_fmt") != "yuv420p":
        raise MediaProbeRejected("pilot requires yuv420p pixel format")
    width, height = stream.get("width"), stream.get("height")
    if (type(width) is not int or type(height) is not int
            or width < 64 or height < 64 or width > MAX_WIDTH or height > MAX_HEIGHT):
        raise MediaProbeRejected("video dimensions exceed pilot policy")
    rate = _fps(stream.get("avg_frame_rate"))
    measured = stream.get("nb_read_frames")
    if not isinstance(measured, str) or not measured.isdecimal():
        raise MediaProbeRejected("ffprobe did not provide counted frames")
    frames = int(measured)
    if frames < 1:
        raise MediaProbeRejected("video has no frames")
    declared = stream.get("nb_frames")
    if declared not in (None, "N/A") and declared != measured:
        raise MediaProbeRejected("MP4 indexed frame count mismatches counted frames")
    if Fraction(frames, 1) / rate > MAX_SECONDS:
        raise MediaProbeRejected("pilot shot longer than 60 seconds")
    if rate.numerator > 1_000_000 or rate.denominator > 1_000_000:
        raise MediaProbeRejected("fps cannot be represented in v1 contract")
    return {"codec": "h264", "pixel_format": "yuv420p", "width": width, "height": height,
            "fps_num": rate.numerator, "fps_den": rate.denominator, "frame_count": frames}


def probe_mp4(media_file: Path) -> dict[str, Any]:
    """對單一本機 MP4 執行 ffprobe（含逐幀計數）與完整解碼到 null sink。

    固定參數、`shell=False`、僅允許 file 協定、不產生任何輸出檔。
    呼叫端負責在前後以 SHA-256 確認檔案未被替換。
    """
    media_file = Path(media_file)
    if media_file.stat().st_size > MAX_VIDEO_BYTES:
        raise MediaProbeRejected("real video probe limited to 128 MiB")
    stdout = _run("ffprobe", [
        "-v", "error", "-protocol_whitelist", "file", "-count_frames",
        "-show_streams", "-show_format", "-of", "json", str(media_file),
    ], PROBE_TIMEOUT)
    try:
        info = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise MediaProbeRejected("ffprobe returned invalid JSON") from exc
    metadata = parse_probe(info)
    _run("ffmpeg", [
        "-hide_banner", "-loglevel", "error", "-nostdin", "-protocol_whitelist", "file",
        "-threads", "1", "-i", str(media_file), "-map", "0:v:0", "-an", "-f", "null", "-",
    ], DECODE_TIMEOUT)
    return metadata
