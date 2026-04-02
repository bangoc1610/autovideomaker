import json
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .constants import (
    DEFAULT_AUDIO_CODEC,
    DEFAULT_PIXEL_FORMAT,
    DEFAULT_PRESET,
    FFMPEG_BIN,
    FFPROBE_BIN,
    VIDEO_ENCODER_AMF,
    VIDEO_ENCODER_AUTO,
    VIDEO_ENCODER_CPU,
    VIDEO_ENCODER_NVENC,
    VIDEO_ENCODER_QSV,
    VIDEO_ENCODER_VIDEOTOOLBOX,
)
from .models import MediaFileInfo


LogFn = Optional[Callable[[str], None]]
ShouldStopFn = Optional[Callable[[], bool]]
OnProcessFn = Optional[Callable[[Optional[subprocess.Popen[str]]], None]]


class FFmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoEncodeProfile:
    """Resolved encoder: codec name + extra ffmpeg args (quality / preset)."""

    codec: str
    extra_args: tuple[str, ...]
    display_name: str


def probe_available_video_encoders() -> set[str]:
    """Return encoder ids present in this ffmpeg build (e.g. h264_nvenc, libx264)."""
    try:
        result = subprocess.run(
            [FFMPEG_BIN, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except OSError:
        return set()
    text = (result.stdout or "") + (result.stderr or "")
    found: set[str] = set()
    for name in (
        "h264_nvenc",
        "h264_qsv",
        "h264_amf",
        "h264_videotoolbox",
        "libx264",
    ):
        if name in text:
            found.add(name)
    return found


def _profile_cpu() -> VideoEncodeProfile:
    return VideoEncodeProfile(
        codec="libx264",
        extra_args=("-preset", DEFAULT_PRESET, "-crf", "23"),
        display_name="CPU (libx264, CRF 23)",
    )


def _profile_nvenc() -> VideoEncodeProfile:
    # Avoid -b:v 0: many Windows FFmpeg/NVENC builds reject it and fail with "Conversion failed".
    # constqp + qp is widely supported; p4 = balanced preset (p1–p7 on recent FFmpeg).
    return VideoEncodeProfile(
        codec="h264_nvenc",
        extra_args=("-preset", "p4", "-rc", "constqp", "-qp", "23"),
        display_name="NVIDIA NVENC (h264_nvenc)",
    )


def cpu_encode_profile() -> VideoEncodeProfile:
    """Public: CPU fallback profile (libx264)."""
    return _profile_cpu()


def _profile_qsv() -> VideoEncodeProfile:
    return VideoEncodeProfile(
        codec="h264_qsv",
        extra_args=("-preset", "medium", "-global_quality", "23"),
        display_name="Intel Quick Sync (h264_qsv)",
    )


def _profile_amf() -> VideoEncodeProfile:
    return VideoEncodeProfile(
        codec="h264_amf",
        extra_args=("-quality", "balanced", "-rc", "cqp", "-qp_i", "23", "-qp_p", "23"),
        display_name="AMD AMF (h264_amf)",
    )


def _profile_videotoolbox() -> VideoEncodeProfile:
    return VideoEncodeProfile(
        codec="h264_videotoolbox",
        extra_args=("-q:v", "65", "-allow_sw", "1"),
        display_name="Apple VideoToolbox (h264_videotoolbox)",
    )


def build_encoder_try_chain(available: set[str], preference: str) -> list[VideoEncodeProfile]:
    """
    First successful encode locks the encoder for that output file.

    - **Auto**: try every *available* GPU encoder in order, **CPU (libx264) only at the end**
      if all GPUs fail. Auto order is QSV → NVENC → AMF → VideoToolbox (QSV first helps
      Intel+NVIDIA hybrid laptops where NVENC may fail on driver/CUDA).
    - **Explicit GPU**: that encoder first, then other GPUs in NVENC→… order, then CPU.
    - **CPU only**: libx264 alone.
    """
    pref = (preference or VIDEO_ENCODER_AUTO).strip().lower()
    if pref == VIDEO_ENCODER_CPU:
        return [_profile_cpu()]

    # Auto: all GPUs first (QSV before NVENC), libx264 strictly last.
    if pref == VIDEO_ENCODER_AUTO:
        order_defs: list[tuple[str, Callable[[], VideoEncodeProfile]]] = [
            ("h264_qsv", _profile_qsv),
            ("h264_nvenc", _profile_nvenc),
            ("h264_amf", _profile_amf),
            ("h264_videotoolbox", _profile_videotoolbox),
        ]
    else:
        order_defs = [
            ("h264_nvenc", _profile_nvenc),
            ("h264_qsv", _profile_qsv),
            ("h264_amf", _profile_amf),
            ("h264_videotoolbox", _profile_videotoolbox),
        ]

    gpu_pairs: list[tuple[str, Callable[[], VideoEncodeProfile]]] = [
        (name, factory) for name, factory in order_defs if name in available
    ]

    pref_to_codec = {
        VIDEO_ENCODER_NVENC: "h264_nvenc",
        VIDEO_ENCODER_QSV: "h264_qsv",
        VIDEO_ENCODER_AMF: "h264_amf",
        VIDEO_ENCODER_VIDEOTOOLBOX: "h264_videotoolbox",
    }
    if pref in pref_to_codec and pref != VIDEO_ENCODER_AUTO:
        want = pref_to_codec[pref]
        gpu_pairs.sort(key=lambda x: (0 if x[0] == want else 1, x[0]))

    chain: list[VideoEncodeProfile] = [factory() for _, factory in gpu_pairs]
    # CPU always last when user did not choose CPU-only (after every GPU option exhausted).
    chain.append(_profile_cpu())
    return chain


def resolve_video_encoder(
    preference: str,
    available: set[str],
    log_fn: LogFn = None,
) -> VideoEncodeProfile:
    """Pick HW encoder by user setting; fall back to libx264 if missing."""

    def log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    pref = (preference or VIDEO_ENCODER_AUTO).strip().lower()

    def fallback(reason: str) -> VideoEncodeProfile:
        log(reason)
        p = _profile_cpu()
        log(f"Using video encoder: {p.display_name}")
        return p

    if pref == VIDEO_ENCODER_AUTO:
        if "h264_nvenc" in available:
            p = _profile_nvenc()
            log(f"Video encoder (auto): {p.display_name}")
            return p
        if "h264_qsv" in available:
            p = _profile_qsv()
            log(f"Video encoder (auto): {p.display_name}")
            return p
        if "h264_amf" in available:
            p = _profile_amf()
            log(f"Video encoder (auto): {p.display_name}")
            return p
        if "h264_videotoolbox" in available:
            p = _profile_videotoolbox()
            log(f"Video encoder (auto): {p.display_name}")
            return p
        p = _profile_cpu()
        log("Video encoder (auto): no GPU H.264 encoder in FFmpeg; using CPU.")
        log(f"Using video encoder: {p.display_name}")
        return p

    if pref == VIDEO_ENCODER_CPU:
        p = _profile_cpu()
        log(f"Using video encoder: {p.display_name}")
        return p

    if pref == VIDEO_ENCODER_NVENC:
        if "h264_nvenc" in available:
            p = _profile_nvenc()
            log(f"Using video encoder: {p.display_name}")
            return p
        return fallback("NVIDIA NVENC not available in this FFmpeg build; falling back to CPU.")

    if pref == VIDEO_ENCODER_QSV:
        if "h264_qsv" in available:
            p = _profile_qsv()
            log(f"Using video encoder: {p.display_name}")
            return p
        return fallback("Intel QSV (h264_qsv) not available; falling back to CPU.")

    if pref == VIDEO_ENCODER_AMF:
        if "h264_amf" in available:
            p = _profile_amf()
            log(f"Using video encoder: {p.display_name}")
            return p
        return fallback("AMD AMF not available; falling back to CPU.")

    if pref == VIDEO_ENCODER_VIDEOTOOLBOX:
        if "h264_videotoolbox" in available:
            p = _profile_videotoolbox()
            log(f"Using video encoder: {p.display_name}")
            return p
        return fallback("VideoToolbox not available; falling back to CPU.")

    p = _profile_cpu()
    log(f"Unknown encoder preference {preference!r}; using {p.display_name}")
    return p


def _video_encode_argv_tail(profile: VideoEncodeProfile) -> list[str]:
    return ["-c:v", profile.codec, *profile.extra_args, "-pix_fmt", DEFAULT_PIXEL_FORMAT]


def _run_command(
    command: list[str],
    log_fn: LogFn = None,
    should_stop: ShouldStopFn = None,
    on_process: OnProcessFn = None,
) -> str:
    if log_fn:
        log_fn("Run: " + " ".join(command))
    # FFmpeg writes progress to stderr continuously. If stderr is PIPE and nobody reads it,
    # the pipe buffer fills and the process blocks forever (deadlock), especially on Windows.
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        try:
            if process.stderr:
                for line in iter(process.stderr.readline, ""):
                    stderr_lines.append(line)
        except (ValueError, OSError):
            pass

    reader = threading.Thread(target=_drain_stderr, daemon=True)
    reader.start()

    if on_process:
        on_process(process)
    try:
        while process.poll() is None:
            if should_stop and should_stop():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise InterruptedError("render stopped by user")
            time.sleep(0.2)

        process.wait()
        reader.join(timeout=30)
        stderr_output = "".join(stderr_lines)
        if process.returncode != 0:
            if log_fn and stderr_output.strip():
                tail = stderr_output.strip().splitlines()
                for line in tail[-20:]:
                    log_fn(line)
            raise FFmpegError(f"Command failed with code {process.returncode}: {' '.join(command)}")
        if log_fn and stderr_output.strip():
            log_fn(stderr_output.strip().splitlines()[-1])
        return ""
    finally:
        if on_process:
            on_process(None)


def ffmpeg_exists() -> bool:
    try:
        subprocess.run([FFMPEG_BIN, "-version"], capture_output=True, text=True, check=False)
        return True
    except OSError:
        return False


def ffprobe_exists() -> bool:
    try:
        subprocess.run([FFPROBE_BIN, "-version"], capture_output=True, text=True, check=False)
        return True
    except OSError:
        return False


def probe_media(file_path: Path) -> MediaFileInfo:
    command = [
        FFPROBE_BIN,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,r_frame_rate",
        "-of",
        "json",
        str(file_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe failed for file: {file_path}")
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration", 0.0) or 0.0)
    width = 0
    height = 0
    fps = 0.0
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream.get("width", 0) or 0)
            height = int(stream.get("height", 0) or 0)
            fps_raw = str(stream.get("r_frame_rate", "0/1"))
            if "/" in fps_raw:
                num, den = fps_raw.split("/", maxsplit=1)
                try:
                    num_f = float(num)
                    den_f = float(den)
                    fps = num_f / den_f if den_f else 0.0
                except ValueError:
                    fps = 0.0
            break
    return MediaFileInfo(path=file_path, duration=duration, width=width, height=height, fps=fps)


def create_reverse_clip(
    input_clip: Path,
    output_clip: Path,
    profile: VideoEncodeProfile,
    log_fn: LogFn = None,
    should_stop: ShouldStopFn = None,
    on_process: OnProcessFn = None,
) -> None:
    output_clip.parent.mkdir(parents=True, exist_ok=True)
    command = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(input_clip),
        "-an",
        "-vf",
        "reverse",
    ]
    command.extend(_video_encode_argv_tail(profile))
    command.append(str(output_clip))
    _run_command(command, log_fn=log_fn, should_stop=should_stop, on_process=on_process)


def normalize_video_clip(
    input_clip: Path,
    output_clip: Path,
    width: int,
    height: int,
    profile: VideoEncodeProfile,
    fps: Optional[float] = None,
    log_fn: LogFn = None,
    should_stop: ShouldStopFn = None,
    on_process: OnProcessFn = None,
) -> None:
    output_clip.parent.mkdir(parents=True, exist_ok=True)
    scale_crop = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )
    command = [FFMPEG_BIN, "-y", "-i", str(input_clip), "-an", "-vf", scale_crop]
    if fps and fps > 0:
        command.extend(["-r", f"{fps:.3f}"])
    command.extend(_video_encode_argv_tail(profile))
    command.append(str(output_clip))
    _run_command(command, log_fn=log_fn, should_stop=should_stop, on_process=on_process)


def create_concat_list_file(files: list[Path], list_file: Path) -> None:
    list_file.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in files:
        escaped = str(item).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines), encoding="utf-8")


def concat_video_files(
    files: list[Path],
    output_file: Path,
    log_fn: LogFn = None,
    should_stop: ShouldStopFn = None,
    on_process: OnProcessFn = None,
) -> None:
    list_file = output_file.parent / f"{output_file.stem}_video_concat.txt"
    create_concat_list_file(files, list_file)
    command = [
        FFMPEG_BIN,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c:v",
        "copy",
        str(output_file),
    ]
    _run_command(command, log_fn=log_fn, should_stop=should_stop, on_process=on_process)


def concat_audio_files(
    files: list[Path],
    output_file: Path,
    log_fn: LogFn = None,
    should_stop: ShouldStopFn = None,
    on_process: OnProcessFn = None,
) -> None:
    list_file = output_file.parent / f"{output_file.stem}_audio_concat.txt"
    create_concat_list_file(files, list_file)
    command = [
        FFMPEG_BIN,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c:a",
        DEFAULT_AUDIO_CODEC,
        str(output_file),
    ]
    _run_command(command, log_fn=log_fn, should_stop=should_stop, on_process=on_process)


def trim_media(
    input_file: Path,
    output_file: Path,
    duration_seconds: int,
    log_fn: LogFn = None,
    should_stop: ShouldStopFn = None,
    on_process: OnProcessFn = None,
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(input_file),
        "-t",
        str(duration_seconds),
        "-c",
        "copy",
        str(output_file),
    ]
    _run_command(command, log_fn=log_fn, should_stop=should_stop, on_process=on_process)


def normalize_audio_clip(
    input_clip: Path,
    output_clip: Path,
    log_fn: LogFn = None,
    should_stop: ShouldStopFn = None,
    on_process: OnProcessFn = None,
) -> None:
    output_clip.parent.mkdir(parents=True, exist_ok=True)
    command = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(input_clip),
        "-vn",
        "-c:a",
        DEFAULT_AUDIO_CODEC,
        str(output_clip),
    ]
    _run_command(command, log_fn=log_fn, should_stop=should_stop, on_process=on_process)


def mux_video_audio(
    video_file: Path,
    audio_file: Path,
    output_file: Path,
    duration_seconds: int,
    profile: VideoEncodeProfile,
    log_fn: LogFn = None,
    should_stop: ShouldStopFn = None,
    on_process: OnProcessFn = None,
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(video_file),
        "-i",
        str(audio_file),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]
    command.extend(_video_encode_argv_tail(profile))
    command.extend(
        [
            "-c:a",
            DEFAULT_AUDIO_CODEC,
            "-t",
            str(duration_seconds),
            "-shortest",
            str(output_file),
        ]
    )
    _run_command(command, log_fn=log_fn, should_stop=should_stop, on_process=on_process)
