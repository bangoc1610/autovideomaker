# Auto Video Maker

Desktop app render video hang loat tu folder `.mp4` va `.mp3` voi UI PySide6 va FFmpeg.

## Tinh nang

- Chon folder MP4, MP3, output.
- Random clip va nhac cho tung output.
- Ho tro random co lap neu yeu cau lon hon so file co san.
- Loop video/audio den du thoi luong va cat chinh xac.
- Reverse dung nghia tua nguoc thoi gian tung clip (`forward -> reverse`).
- Chon ti le `Keep Original / 16:9 / 9:16`.
- Chon chat luong `Keep Original / 1080p / 2K / 4K`.
- Bo audio goc MP4, chi dung audio MP3.
- Log realtime, progress tong, status hien tai.
- Luu/load settings JSON.
- Stop render an toan, terminate ffmpeg process dang chay.
- Chon encoder video: Auto (GPU uu tien), CPU (libx264), hoac NVIDIA / Intel / AMD / VideoToolbox.

## GPU / tang toc encode

- Trong **Render Settings**, chon **Video encoder**:
  - **Auto**: moi file output thu lan luot **GPU co trong FFmpeg** (thu tu: **QSV → NVENC → AMF → VideoToolbox**), encoder dau tien chay duoc se khoa cho het file; **chi khi tat ca GPU deu loi** moi dung **libx264** (CPU).
  - **CPU – libx264**: on dinh, khong can GPU.
  - **GPU – NVENC / QSV / AMF / VideoToolbox**: encode bang phan cung neu FFmpeg cua ban co build kem encoder do.

- Can **FFmpeg build day du** (tren Windows nhieu ban static da co `h264_nvenc`). Kiem tra:

```powershell
ffmpeg -hide_banner -encoders | findstr h264_nvenc
```

- Scale / crop / filter `reverse` van chay tren CPU; **GPU chu yeu giam tai o buoc encode H.264**. Audio van encode AAC tren CPU (nhe).

## Yeu cau he thong

- Python 3.11+
- FFmpeg + ffprobe trong PATH
- Windows/macOS/Linux

## Cai dat Python

1. Cai Python 3.11 tro len:
   - [https://www.python.org/downloads/](https://www.python.org/downloads/)
2. Tao virtual environment (khuyen nghi):

```bash
python -m venv .venv
```

3. Kich hoat venv:
   - Windows (PowerShell):

```powershell
.venv\Scripts\Activate.ps1
```

   - macOS/Linux:

```bash
source .venv/bin/activate
```

4. Cai dependencies:

```bash
pip install -r requirements.txt
```

## Cai FFmpeg

Can co ca `ffmpeg` va `ffprobe`.

- Windows:
  - Tai ban build FFmpeg static.
  - Them thu muc `bin` vao PATH.
  - Kiem tra:

```powershell
ffmpeg -version
ffprobe -version
```

- macOS:

```bash
brew install ffmpeg
```

- Ubuntu/Debian:

```bash
sudo apt update
sudo apt install ffmpeg
```

## Chay ung dung

Trong thu muc `auto_video_maker`:

```bash
python main.py
```

## Huong dan su dung nhanh

1. Chon `MP4 Folder`, `MP3 Folder`, `Output Folder`.
2. Dat:
   - So file mp4 moi output
   - So file mp3 moi output
   - So video can render
   - Thoi luong output (phut)
   - Aspect ratio, quality, **Video encoder** (GPU/CPU)
   - Reverse neu can
3. Bam `Start Render`.
4. Theo doi progress/status/log realtime.
5. Bam `Stop Render` de dung an toan.

## Luu y ve Reverse

- Reverse la tua nguoc theo thoi gian (`reverse` filter), KHONG phai lat ngang.
- Moi clip se thanh chuoi:
  - clip forward
  - clip reverse
- Sau do moi noi chuoi, loop den du duration va trim.

## Cau truc project

```text
auto_video_maker/
│
├─ main.py
├─ requirements.txt
├─ README.md
├─ app/
│  ├─ __init__.py
│  ├─ constants.py
│  ├─ models.py
│  ├─ config_manager.py
│  ├─ file_utils.py
│  ├─ ffmpeg_utils.py
│  ├─ naming.py
│  ├─ render_planner.py
│  ├─ render_worker.py
│  ├─ ui_main.py
│  └─ main_window.py
│
└─ data/
   └─ settings.json
```
