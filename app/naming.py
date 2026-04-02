import re
from datetime import datetime
from pathlib import Path
from typing import List


INVALID_CHARS_PATTERN = re.compile(r"[<>:\"/\\|?*\x00-\x1F]")
MULTI_UNDERSCORE_PATTERN = re.compile(r"_+")


def sanitize_component(name: str) -> str:
    safe = INVALID_CHARS_PATTERN.sub("_", name)
    safe = safe.replace(" ", "_")
    safe = MULTI_UNDERSCORE_PATTERN.sub("_", safe).strip("._- ")
    return safe or "unnamed"


def build_output_filename(index: int, selected_mp4_files: List[Path]) -> str:
    index_prefix = f"{index:03d}"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    base_names = [sanitize_component(path.stem) for path in selected_mp4_files]
    if not base_names:
        base_names = ["clip"]

    joined = "-".join(base_names)
    max_body_len = 140
    if len(joined) > max_body_len:
        joined = joined[:max_body_len].rstrip("-_")
        if not joined:
            joined = "clip"

    file_name = f"{index_prefix}-{joined}-{timestamp}.mp4"
    if len(file_name) > 200:
        keep_name = sanitize_component(base_names[0])[:32] or "clip"
        file_name = f"{index_prefix}-{keep_name}-{timestamp}.mp4"
    return file_name
