from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .constants import ASPECT_OPTIONS, QUALITY_OPTIONS, VIDEO_ENCODER_CHOICES


class MainUI:
    def setup_ui(self, root: QWidget) -> None:
        root_layout = QVBoxLayout(root)

        # Folder selectors
        folder_group = QGroupBox("Folders")
        folder_layout = QGridLayout(folder_group)
        self.mp4_label = QLabel("MP4 Folder")
        self.mp4_input = QLineEdit()
        self.mp4_browse_btn = QPushButton("Browse")
        folder_layout.addWidget(self.mp4_label, 0, 0)
        folder_layout.addWidget(self.mp4_input, 0, 1)
        folder_layout.addWidget(self.mp4_browse_btn, 0, 2)

        self.mp3_label = QLabel("MP3 Folder")
        self.mp3_input = QLineEdit()
        self.mp3_browse_btn = QPushButton("Browse")
        folder_layout.addWidget(self.mp3_label, 1, 0)
        folder_layout.addWidget(self.mp3_input, 1, 1)
        folder_layout.addWidget(self.mp3_browse_btn, 1, 2)

        self.output_label = QLabel("Output Folder")
        self.output_input = QLineEdit()
        self.output_browse_btn = QPushButton("Browse")
        folder_layout.addWidget(self.output_label, 2, 0)
        folder_layout.addWidget(self.output_input, 2, 1)
        folder_layout.addWidget(self.output_browse_btn, 2, 2)

        # Settings
        settings_group = QGroupBox("Render Settings")
        settings_layout = QFormLayout(settings_group)

        self.mp4_count_spin = QSpinBox()
        self.mp4_count_spin.setRange(1, 9999)
        settings_layout.addRow("MP4 files per output", self.mp4_count_spin)

        self.mp3_count_spin = QSpinBox()
        self.mp3_count_spin.setRange(1, 9999)
        settings_layout.addRow("MP3 files per output", self.mp3_count_spin)

        self.render_count_spin = QSpinBox()
        self.render_count_spin.setRange(1, 9999)
        settings_layout.addRow("Total videos to render", self.render_count_spin)

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 24 * 60)
        settings_layout.addRow("Duration (minutes)", self.duration_spin)

        self.aspect_combo = QComboBox()
        self.aspect_combo.addItems(ASPECT_OPTIONS)
        settings_layout.addRow("Aspect Ratio", self.aspect_combo)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(QUALITY_OPTIONS)
        settings_layout.addRow("Quality", self.quality_combo)

        self.encoder_combo = QComboBox()
        for value, label in VIDEO_ENCODER_CHOICES:
            self.encoder_combo.addItem(label, value)
        settings_layout.addRow("Video encoder", self.encoder_combo)

        self.reverse_checkbox = QCheckBox("Reverse")
        settings_layout.addRow("", self.reverse_checkbox)

        # Action buttons
        action_group = QGroupBox("Actions")
        action_layout = QHBoxLayout(action_group)
        self.start_btn = QPushButton("Start Render")
        self.stop_btn = QPushButton("Stop Render")
        self.save_btn = QPushButton("Save Settings")
        self.load_btn = QPushButton("Load Settings")
        self.open_output_btn = QPushButton("Open Output Folder")
        action_layout.addWidget(self.start_btn)
        action_layout.addWidget(self.stop_btn)
        action_layout.addWidget(self.save_btn)
        action_layout.addWidget(self.load_btn)
        action_layout.addWidget(self.open_output_btn)

        # Status / logs
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout(status_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFormat("%p% — tiến độ tổng (cả batch)")
        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QTextEdit.NoWrap)
        self.log_text.setMinimumHeight(240)
        self.log_text.setAcceptRichText(False)
        self.log_text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        status_layout.addWidget(self.progress_bar)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.log_text)

        root_layout.addWidget(folder_group)
        root_layout.addWidget(settings_group)
        root_layout.addWidget(action_group)
        root_layout.addWidget(status_group)
