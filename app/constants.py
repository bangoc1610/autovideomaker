from pathlib import Path

APP_NAME = "Auto Video Maker"
WINDOW_TITLE = "Auto Video Maker - Batch Render Tool"

ASPECT_KEEP = "Keep Original"
ASPECT_16_9 = "16:9"
ASPECT_9_16 = "9:16"
ASPECT_OPTIONS = [ASPECT_KEEP, ASPECT_16_9, ASPECT_9_16]

QUALITY_KEEP = "Keep Original"
QUALITY_1080P = "1080p"
QUALITY_2K = "2K"
QUALITY_4K = "4K"
QUALITY_OPTIONS = [QUALITY_KEEP, QUALITY_1080P, QUALITY_2K, QUALITY_4K]

DEFAULT_MP4_COUNT = 3
DEFAULT_MP3_COUNT = 3
DEFAULT_RENDER_COUNT = 1
DEFAULT_DURATION_MINUTES = 30
DEFAULT_REVERSE = False

DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_PIXEL_FORMAT = "yuv420p"
DEFAULT_PRESET = "medium"

# Stored in settings JSON (value -> UI label in combo)
VIDEO_ENCODER_AUTO = "auto"
VIDEO_ENCODER_CPU = "cpu"
VIDEO_ENCODER_NVENC = "nvenc"
VIDEO_ENCODER_QSV = "qsv"
VIDEO_ENCODER_AMF = "amf"
VIDEO_ENCODER_VIDEOTOOLBOX = "videotoolbox"
DEFAULT_VIDEO_ENCODER = VIDEO_ENCODER_AUTO

VIDEO_ENCODER_CHOICES: list[tuple[str, str]] = [
    (VIDEO_ENCODER_AUTO, "Auto — thử hết GPU (QSV→NVENC→…), cuối cùng mới CPU"),
    (VIDEO_ENCODER_CPU, "CPU – libx264"),
    (VIDEO_ENCODER_NVENC, "GPU – NVIDIA NVENC (h264_nvenc)"),
    (VIDEO_ENCODER_QSV, "GPU – Intel Quick Sync (h264_qsv)"),
    (VIDEO_ENCODER_AMF, "GPU – AMD AMF (h264_amf)"),
    (VIDEO_ENCODER_VIDEOTOOLBOX, "GPU – Apple VideoToolbox (macOS)"),
]

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v"}
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac"}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"

FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"

QUALITY_DIMENSIONS = {
    ASPECT_16_9: {
        QUALITY_1080P: (1920, 1080),
        QUALITY_2K: (2560, 1440),
        QUALITY_4K: (3840, 2160),
    },
    ASPECT_9_16: {
        QUALITY_1080P: (1080, 1920),
        QUALITY_2K: (1440, 2560),
        QUALITY_4K: (2160, 3840),
    },
}
