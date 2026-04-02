import random
from pathlib import Path
from typing import List, Optional, Tuple

from .constants import ASPECT_KEEP, QUALITY_DIMENSIONS, QUALITY_KEEP
from .file_utils import random_pick_files
from .models import AppSettings, MediaFileInfo, RenderJob, RenderPlan
from .naming import build_output_filename


def _loop_files_to_duration(
    files: List[str],
    duration_lookup: dict[str, float],
    target_seconds: int,
) -> List[str]:
    if target_seconds <= 0:
        return []
    timeline: List[str] = []
    total = 0.0
    if not files:
        return timeline
    idx = 0
    while total < target_seconds:
        clip = files[idx % len(files)]
        timeline.append(clip)
        total += duration_lookup.get(clip, 0.0)
        idx += 1
        if idx > 100000:
            break
    return timeline


def resolve_target_dimensions(
    settings: AppSettings, first_video_info: MediaFileInfo
) -> Tuple[int, int]:
    first_w = max(1, first_video_info.width)
    first_h = max(1, first_video_info.height)
    if settings.aspect_ratio == ASPECT_KEEP and settings.quality == QUALITY_KEEP:
        return first_w, first_h

    if settings.aspect_ratio in QUALITY_DIMENSIONS and settings.quality == QUALITY_KEEP:
        # Keep current quality scale but enforce selected aspect by using first-video height.
        if settings.aspect_ratio == "16:9":
            target_h = first_h
            target_w = int(round(target_h * 16 / 9))
        else:
            target_h = first_h
            target_w = int(round(target_h * 9 / 16))
        if target_w % 2 != 0:
            target_w += 1
        if target_h % 2 != 0:
            target_h += 1
        return max(2, target_w), max(2, target_h)

    if settings.aspect_ratio in QUALITY_DIMENSIONS and settings.quality in QUALITY_DIMENSIONS[settings.aspect_ratio]:
        return QUALITY_DIMENSIONS[settings.aspect_ratio][settings.quality]

    # Keep original ratio but map to nearest dimensions by selected quality height.
    if settings.quality != QUALITY_KEEP:
        target_base_map = {
            "1080p": 1080,
            "2K": 1440,
            "4K": 2160,
        }
        target_h = target_base_map.get(settings.quality, first_h)
        ratio = first_w / first_h
        target_w = int(round(target_h * ratio))
        # Ensure width is even for encoder compatibility.
        if target_w % 2 != 0:
            target_w += 1
        if target_h % 2 != 0:
            target_h += 1
        return max(2, target_w), max(2, target_h)

    return first_w, first_h


def create_render_job(
    settings: AppSettings,
    index: int,
    mp4_pool: List[Path],
    mp3_pool: List[Path],
    output_folder: Path,
    rng: Optional[random.Random] = None,
) -> tuple[RenderJob, dict[str, bool]]:
    random_engine = rng or random.Random()
    selected_mp4, repeated_mp4 = random_pick_files(mp4_pool, settings.mp4_count, random_engine)
    selected_mp3, repeated_mp3 = random_pick_files(mp3_pool, settings.mp3_count, random_engine)
    filename = build_output_filename(index=index, selected_mp4_files=selected_mp4)

    job = RenderJob(
        index=index,
        selected_mp4_files=selected_mp4,
        selected_mp3_files=selected_mp3,
        output_path=output_folder / filename,
        target_duration_seconds=settings.duration_minutes * 60,
        temp_dir=output_folder / f"_temp_job_{index:03d}",
    )
    return job, {"mp4_with_replacement": repeated_mp4, "mp3_with_replacement": repeated_mp3}


def build_render_plan(
    settings: AppSettings,
    job: RenderJob,
    video_info_map: dict[Path, MediaFileInfo],
    audio_info_map: dict[Path, MediaFileInfo],
) -> tuple[RenderPlan, List[str], List[Path]]:
    warnings: List[str] = []
    first_info = video_info_map[job.selected_mp4_files[0]]
    target_w, target_h = resolve_target_dimensions(settings, first_info)

    video_base: List[str] = []
    for clip in job.selected_mp4_files:
        video_base.append(str(clip))
        if settings.reverse_enabled:
            reverse_marker = f"__REVERSE__|{clip}"
            video_base.append(reverse_marker)

    duration_lookup_video = {}
    for clip in video_base:
        source = Path(clip.replace("__REVERSE__|", ""))
        duration_lookup_video[clip] = max(0.01, video_info_map[source].duration)

    duration_lookup_audio = {clip: max(0.01, audio_info_map[clip].duration) for clip in job.selected_mp3_files}

    looped_video = _loop_files_to_duration(
        files=video_base,
        duration_lookup=duration_lookup_video,
        target_seconds=job.target_duration_seconds,
    )
    looped_audio = _loop_files_to_duration(
        files=job.selected_mp3_files,
        duration_lookup=duration_lookup_audio,
        target_seconds=job.target_duration_seconds,
    )

    plan = RenderPlan(
        job=job,
        target_width=target_w,
        target_height=target_h,
        target_fps=first_info.fps if first_info.fps > 0 else None,
        warnings=warnings,
    )
    return plan, looped_video, looped_audio
