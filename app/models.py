from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .constants import (
    ASPECT_KEEP,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_MP3_COUNT,
    DEFAULT_MP4_COUNT,
    DEFAULT_RENDER_COUNT,
    DEFAULT_REVERSE,
    QUALITY_KEEP,
)


@dataclass
class AppSettings:
    mp4_folder: str = ""
    mp3_folder: str = ""
    output_folder: str = ""
    mp4_count: int = DEFAULT_MP4_COUNT
    mp3_count: int = DEFAULT_MP3_COUNT
    render_count: int = DEFAULT_RENDER_COUNT
    duration_minutes: int = DEFAULT_DURATION_MINUTES
    aspect_ratio: str = ASPECT_KEEP
    quality: str = QUALITY_KEEP
    reverse_enabled: bool = DEFAULT_REVERSE


@dataclass
class MediaFileInfo:
    path: Path
    duration: float
    width: int = 0
    height: int = 0
    fps: float = 0.0


@dataclass
class RenderJob:
    index: int
    selected_mp4_files: List[Path]
    selected_mp3_files: List[Path]
    output_path: Path
    target_duration_seconds: int
    temp_dir: Path


@dataclass
class RenderPlan:
    job: RenderJob
    target_width: int
    target_height: int
    target_fps: Optional[float] = None
    warnings: List[str] = field(default_factory=list)
