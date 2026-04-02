import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .constants import (
    DEFAULT_AUDIO_CODEC,
    DEFAULT_PIXEL_FORMAT,
    DEFAULT_PRESET,
    DEFAULT_VIDEO_CODEC,
    FFMPEG_BIN,
    FFPROBE_BIN,
)
from .models import MediaFileInfo


LogFn = Optional[Callable[[str], None]]
ShouldStopFn = Optional[Callable[[], bool]]
OnProcessFn = Optional[Callable[[Optional[subprocess.Popen[str]]], None]]


class FFmpegError(RuntimeError):
    pass


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
                for line in tail[-5:]:
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
        "-c:v",
        DEFAULT_VIDEO_CODEC,
        "-preset",
        DEFAULT_PRESET,
        "-pix_fmt",
        DEFAULT_PIXEL_FORMAT,
        str(output_clip),
    ]
    _run_command(command, log_fn=log_fn, should_stop=should_stop, on_process=on_process)


def normalize_video_clip(
    input_clip: Path,
    output_clip: Path,
    width: int,
    height: int,
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
    command.extend(
        [
            "-c:v",
            DEFAULT_VIDEO_CODEC,
            "-preset",
            DEFAULT_PRESET,
            "-pix_fmt",
            DEFAULT_PIXEL_FORMAT,
            str(output_clip),
        ]
    )
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
        "-c:v",
        DEFAULT_VIDEO_CODEC,
        "-preset",
        DEFAULT_PRESET,
        "-pix_fmt",
        DEFAULT_PIXEL_FORMAT,
        "-c:a",
        DEFAULT_AUDIO_CODEC,
        "-t",
        str(duration_seconds),
        "-shortest",
        str(output_file),
    ]
    _run_command(command, log_fn=log_fn, should_stop=should_stop, on_process=on_process)
