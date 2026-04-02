import json
from dataclasses import asdict
from pathlib import Path

from .constants import SETTINGS_FILE
from .models import AppSettings


class ConfigManager:
    def __init__(self, settings_path: Path = SETTINGS_FILE) -> None:
        self.settings_path = settings_path

    def load_settings(self) -> AppSettings:
        if not self.settings_path.exists():
            return AppSettings()
        try:
            raw_data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            default_data = asdict(AppSettings())
            merged = {**default_data, **raw_data}
            return AppSettings(**merged)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return AppSettings()

    def save_settings(self, settings: AppSettings) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(settings)
        self.settings_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
