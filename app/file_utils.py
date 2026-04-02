import random
from pathlib import Path
from typing import List, Sequence, Tuple

from .constants import SUPPORTED_AUDIO_EXTENSIONS, SUPPORTED_VIDEO_EXTENSIONS


def validate_folder_exists(folder: str) -> Tuple[bool, str]:
    folder_path = Path(folder).expanduser()
    if not folder.strip():
        return False, "Path is empty."
    if not folder_path.exists():
        return False, f"Folder does not exist: {folder_path}"
    if not folder_path.is_dir():
        return False, f"Path is not a directory: {folder_path}"
    return True, ""


def ensure_output_folder(folder: str) -> Path:
    output_path = Path(folder).expanduser()
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path


def scan_media_files(folder: str, extensions: set[str]) -> List[Path]:
    folder_path = Path(folder).expanduser()
    if not folder_path.exists() or not folder_path.is_dir():
        return []
    files = [
        file_path
        for file_path in folder_path.rglob("*")
        if file_path.is_file() and file_path.suffix.lower() in extensions
    ]
    files.sort(key=lambda p: p.name.lower())
    return files


def scan_mp4_files(folder: str) -> List[Path]:
    return scan_media_files(folder, SUPPORTED_VIDEO_EXTENSIONS)


def scan_mp3_files(folder: str) -> List[Path]:
    return scan_media_files(folder, SUPPORTED_AUDIO_EXTENSIONS)


def random_pick_files(
    files: Sequence[Path], count: int, rng: random.Random
) -> Tuple[List[Path], bool]:
    if count <= 0:
        return [], False
    if not files:
        return [], False
    if count <= len(files):
        return rng.sample(list(files), count), False
    return [rng.choice(files) for _ in range(count)], True
