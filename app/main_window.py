import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QWidget,
)

from .config_manager import ConfigManager
from .constants import APP_NAME, DEFAULT_VIDEO_ENCODER, WINDOW_TITLE
from .file_utils import ensure_output_folder, scan_mp3_files, scan_mp4_files, validate_folder_exists
from .models import AppSettings
from .render_coordinator import RenderCoordinator
from .ui_main import MainUI


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1100, 800)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        self.ui = MainUI()
        self.ui.setup_ui(central_widget)
        self.config_manager = ConfigManager()
        self.coordinator: RenderCoordinator | None = None

        self._connect_signals()
        self._load_initial_settings()
        self._set_render_running(False)

    def _connect_signals(self) -> None:
        self.ui.mp4_browse_btn.clicked.connect(lambda: self._browse_folder(self.ui.mp4_input))
        self.ui.mp3_browse_btn.clicked.connect(lambda: self._browse_folder(self.ui.mp3_input))
        self.ui.output_browse_btn.clicked.connect(lambda: self._browse_folder(self.ui.output_input))

        self.ui.start_btn.clicked.connect(self.start_render)
        self.ui.stop_btn.clicked.connect(self.stop_render)
        self.ui.save_btn.clicked.connect(self.save_settings)
        self.ui.load_btn.clicked.connect(self.load_settings)
        self.ui.open_output_btn.clicked.connect(self.open_output_folder)

    def _append_log(self, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.ui.log_text.append(f"[{timestamp}] {text}")
        cursor = self.ui.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.ui.log_text.setTextCursor(cursor)

    def _set_render_running(self, running: bool) -> None:
        self.ui.start_btn.setEnabled(not running)
        self.ui.stop_btn.setEnabled(running)
        self.ui.save_btn.setEnabled(not running)
        self.ui.load_btn.setEnabled(not running)

    def _load_initial_settings(self) -> None:
        settings = self.config_manager.load_settings()
        self._apply_settings_to_ui(settings)
        self._append_log("Loaded latest settings.")
        self._warn_missing_paths(settings)

    def _warn_missing_paths(self, settings: AppSettings) -> None:
        for label, folder in (
            ("mp4_folder", settings.mp4_folder),
            ("mp3_folder", settings.mp3_folder),
            ("output_folder", settings.output_folder),
        ):
            if folder and not Path(folder).expanduser().exists():
                self._append_log(f"Warning: saved path not found ({label}): {folder}")

    def _browse_folder(self, line_edit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            line_edit.setText(folder)

    def _collect_settings_from_ui(self) -> AppSettings:
        return AppSettings(
            mp4_folder=self.ui.mp4_input.text().strip(),
            mp3_folder=self.ui.mp3_input.text().strip(),
            output_folder=self.ui.output_input.text().strip(),
            mp4_count=self.ui.mp4_count_spin.value(),
            mp3_count=self.ui.mp3_count_spin.value(),
            render_count=self.ui.render_count_spin.value(),
            duration_minutes=self.ui.duration_spin.value(),
            aspect_ratio=self.ui.aspect_combo.currentText(),
            quality=self.ui.quality_combo.currentText(),
            reverse_enabled=self.ui.reverse_checkbox.isChecked(),
            video_encoder=self.ui.encoder_combo.currentData() or DEFAULT_VIDEO_ENCODER,
        )

    def _apply_settings_to_ui(self, settings: AppSettings) -> None:
        self.ui.mp4_input.setText(settings.mp4_folder)
        self.ui.mp3_input.setText(settings.mp3_folder)
        self.ui.output_input.setText(settings.output_folder)
        self.ui.mp4_count_spin.setValue(settings.mp4_count)
        self.ui.mp3_count_spin.setValue(settings.mp3_count)
        self.ui.render_count_spin.setValue(settings.render_count)
        self.ui.duration_spin.setValue(settings.duration_minutes)
        self.ui.aspect_combo.setCurrentText(settings.aspect_ratio)
        self.ui.quality_combo.setCurrentText(settings.quality)
        enc = settings.video_encoder or DEFAULT_VIDEO_ENCODER
        enc_idx = self.ui.encoder_combo.findData(enc)
        if enc_idx >= 0:
            self.ui.encoder_combo.setCurrentIndex(enc_idx)
        else:
            self.ui.encoder_combo.setCurrentIndex(0)
        self.ui.reverse_checkbox.setChecked(settings.reverse_enabled)

    def save_settings(self) -> None:
        settings = self._collect_settings_from_ui()
        try:
            self.config_manager.save_settings(settings)
            self._append_log("Settings saved.")
        except Exception as ex:
            QMessageBox.critical(self, APP_NAME, f"Failed to save settings:\n{ex}")
            self._append_log(f"Failed to save settings: {ex}")

    def load_settings(self) -> None:
        settings = self.config_manager.load_settings()
        self._apply_settings_to_ui(settings)
        self._append_log("Settings loaded.")
        self._warn_missing_paths(settings)

    def _validate_inputs(self, settings: AppSettings) -> tuple[bool, list[str]]:
        errors: list[str] = []
        valid, msg = validate_folder_exists(settings.mp4_folder)
        if not valid:
            errors.append(f"MP4 folder error: {msg}")
        valid, msg = validate_folder_exists(settings.mp3_folder)
        if not valid:
            errors.append(f"MP3 folder error: {msg}")
        if not settings.output_folder:
            errors.append("Output folder is empty.")
        return len(errors) == 0, errors

    def start_render(self) -> None:
        if not self.ui.start_btn.isEnabled():
            self._append_log("Render is already running.")
            return

        settings = self._collect_settings_from_ui()
        ok, errors = self._validate_inputs(settings)
        if not ok:
            QMessageBox.warning(self, APP_NAME, "\n".join(errors))
            for err in errors:
                self._append_log(err)
            return

        output_path = ensure_output_folder(settings.output_folder)
        self._append_log(f"Output folder ready: {output_path}")
        self._append_log("Scanning mp4 folder...")
        mp4_files = scan_mp4_files(settings.mp4_folder)
        self._append_log(f"Found mp4 files: {len(mp4_files)}")
        self._append_log("Scanning mp3 folder...")
        mp3_files = scan_mp3_files(settings.mp3_folder)
        self._append_log(f"Found mp3 files: {len(mp3_files)}")

        if not mp4_files:
            QMessageBox.warning(self, APP_NAME, "No mp4 files found.")
            return
        if not mp3_files:
            QMessageBox.warning(self, APP_NAME, "No mp3 files found.")
            return

        self.save_settings()

        # Dual GPU coordinator (QSV + NVENC) for stable parallel batch render.
        # When both GPUs fail, coordinator will switch to CPU automatically.
        self.coordinator = RenderCoordinator(
            settings=settings,
            mp4_files=mp4_files,
            mp3_files=mp3_files,
            log_cb=self._append_log,
            status_cb=self.ui.status_label.setText,
            progress_cb=self.ui.progress_bar.setValue,
            on_finished=self._on_coordinator_finished,
        )
        self._set_render_running(True)
        self.ui.progress_bar.setValue(0)
        self.ui.status_label.setText("Render started...")
        self._append_log("Render queue started.")
        self.coordinator.start()

    def stop_render(self) -> None:
        if self.coordinator:
            self.coordinator.stop()
            self.ui.status_label.setText("Stopping render...")
            self._append_log("Stopping render...")

    def _on_coordinator_finished(self) -> None:
        self._set_render_running(False)
        self.ui.status_label.setText("Ready.")
        self._append_log("Render finished.")

    def open_output_folder(self) -> None:
        path_text = self.ui.output_input.text().strip()
        if not path_text:
            QMessageBox.information(self, APP_NAME, "Output folder is empty.")
            return
        path = Path(path_text).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            import os

            os.startfile(str(path))
        elif sys.platform == "darwin":
            import subprocess

            subprocess.run(["open", str(path)], check=False)
        else:
            import subprocess

            subprocess.run(["xdg-open", str(path)], check=False)


def run_app() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    app.exec()
