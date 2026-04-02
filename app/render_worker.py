import subprocess
import shutil
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .ffmpeg_utils import (
    FFmpegError,
    concat_audio_files,
    concat_video_files,
    create_reverse_clip,
    ffmpeg_exists,
    ffprobe_exists,
    mux_video_audio,
    normalize_audio_clip,
    normalize_video_clip,
    probe_media,
    trim_media,
)
from .models import AppSettings
from .render_planner import build_render_plan, create_render_job


class RenderWorker(QThread):
    log_signal = Signal(str)
    progress_signal = Signal(int)
    status_signal = Signal(str)
    finished_signal = Signal()
    error_signal = Signal(str)

    def __init__(self, settings: AppSettings, mp4_files: list[Path], mp3_files: list[Path]) -> None:
        super().__init__()
        self.settings = settings
        self.mp4_files = mp4_files
        self.mp3_files = mp3_files
        self._stop_requested = False
        self._current_process: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        self._stop_requested = True
        if self._current_process and self._current_process.poll() is None:
            self._current_process.terminate()
        self.log_signal.emit("Render stop requested by user.")

    def _check_stop(self) -> None:
        if self._stop_requested:
            raise InterruptedError("render stopped by user")

    def _log(self, message: str) -> None:
        self.log_signal.emit(message)

    def _on_process(self, process: subprocess.Popen[str] | None) -> None:
        self._current_process = process

    def _emit_job_progress(self, job_index: int, step: int, total_steps: int) -> None:
        """Map sub-steps inside one output to overall 0–100 progress bar."""
        if total_steps <= 0:
            return
        total_jobs = max(1, self.settings.render_count)
        # Fraction of this job completed: (step+1)/total_steps
        job_fraction = (job_index - 1) / total_jobs + (step + 1) / total_steps / total_jobs
        pct = min(99, int(job_fraction * 100))
        self.progress_signal.emit(pct)

    def run(self) -> None:
        try:
            self.status_signal.emit("Checking FFmpeg...")
            if not ffmpeg_exists():
                raise FFmpegError("ffmpeg not found. Please install FFmpeg and add to PATH.")
            if not ffprobe_exists():
                raise FFmpegError("ffprobe not found. Please install FFmpeg and add to PATH.")

            video_info_map = {}
            audio_info_map = {}

            self.status_signal.emit("Reading media metadata...")
            for file_path in self.mp4_files:
                self._check_stop()
                try:
                    video_info_map[file_path] = probe_media(file_path)
                except Exception as ex:
                    self._log(f"Skip invalid mp4 metadata: {file_path.name} ({ex})")

            for file_path in self.mp3_files:
                self._check_stop()
                try:
                    audio_info_map[file_path] = probe_media(file_path)
                except Exception as ex:
                    self._log(f"Skip invalid mp3 metadata: {file_path.name} ({ex})")

            valid_mp4 = [p for p in self.mp4_files if p in video_info_map and video_info_map[p].duration > 0]
            valid_mp3 = [p for p in self.mp3_files if p in audio_info_map and audio_info_map[p].duration > 0]
            if not valid_mp4:
                raise FFmpegError("No valid mp4 files with readable metadata.")
            if not valid_mp3:
                raise FFmpegError("No valid mp3 files with readable metadata.")

            output_root = Path(self.settings.output_folder).expanduser()
            output_root.mkdir(parents=True, exist_ok=True)

            for idx in range(1, self.settings.render_count + 1):
                self._check_stop()
                progress = int(((idx - 1) / self.settings.render_count) * 100)
                self.progress_signal.emit(progress)
                self.status_signal.emit(f"Rendering {idx}/{self.settings.render_count}")
                self._log(f"=== Start output {idx}/{self.settings.render_count} ===")

                job, replacement_info = create_render_job(
                    settings=self.settings,
                    index=idx,
                    mp4_pool=valid_mp4,
                    mp3_pool=valid_mp3,
                    output_folder=output_root,
                )
                if replacement_info["mp4_with_replacement"]:
                    self._log("MP4 random uses replacement (requested > available).")
                if replacement_info["mp3_with_replacement"]:
                    self._log("MP3 random uses replacement (requested > available).")
                self._log("Selected mp4: " + ", ".join(p.name for p in job.selected_mp4_files))
                self._log("Selected mp3: " + ", ".join(p.name for p in job.selected_mp3_files))

                plan, video_timeline, audio_timeline = build_render_plan(
                    settings=self.settings,
                    job=job,
                    video_info_map=video_info_map,
                    audio_info_map=audio_info_map,
                )

                # Steps: each video part + each audio part + concat v + concat a + trim v + trim a + mux
                total_steps = (
                    len(video_timeline)
                    + len(audio_timeline)
                    + 5
                )
                total_steps = max(1, total_steps)
                step_counter = 0

                temp_dir = plan.job.temp_dir
                if temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
                temp_dir.mkdir(parents=True, exist_ok=True)

                normalized_video_clips: list[Path] = []
                normalized_audio_clips: list[Path] = []

                try:
                    v_total = len(video_timeline)
                    a_total = len(audio_timeline)
                    for clip_idx, timeline_entry in enumerate(video_timeline, start=1):
                        self._check_stop()
                        is_reverse = timeline_entry.startswith("__REVERSE__|")
                        src = Path(timeline_entry.replace("__REVERSE__|", ""))
                        base_name = f"v_{clip_idx:04d}_{src.stem}"
                        if is_reverse:
                            self.status_signal.emit(
                                f"Output {idx}/{self.settings.render_count} · "
                                f"Reverse {clip_idx}/{v_total} · {src.name}"
                            )
                            self._log(f"Creating reverse clip for: {src.name}")
                            reversed_clip = temp_dir / f"{base_name}_rev_raw.mp4"
                            create_reverse_clip(
                                src,
                                reversed_clip,
                                log_fn=self._log,
                                should_stop=lambda: self._stop_requested,
                                on_process=self._on_process,
                            )
                            source_for_normalize = reversed_clip
                        else:
                            source_for_normalize = src
                        self.status_signal.emit(
                            f"Output {idx}/{self.settings.render_count} · "
                            f"Encode video {clip_idx}/{v_total} · {src.name}"
                        )
                        normalized = temp_dir / f"{base_name}_norm.mp4"
                        normalize_video_clip(
                            input_clip=source_for_normalize,
                            output_clip=normalized,
                            width=plan.target_width,
                            height=plan.target_height,
                            fps=plan.target_fps,
                            log_fn=self._log,
                            should_stop=lambda: self._stop_requested,
                            on_process=self._on_process,
                        )
                        normalized_video_clips.append(normalized)
                        self._emit_job_progress(idx, step_counter, total_steps)
                        step_counter += 1

                    for audio_idx, audio_src in enumerate(audio_timeline, start=1):
                        self._check_stop()
                        self.status_signal.emit(
                            f"Output {idx}/{self.settings.render_count} · "
                            f"Audio {audio_idx}/{a_total} · {audio_src.name}"
                        )
                        out_audio = temp_dir / f"a_{audio_idx:04d}_{audio_src.stem}.m4a"
                        normalize_audio_clip(
                            input_clip=audio_src,
                            output_clip=out_audio,
                            log_fn=self._log,
                            should_stop=lambda: self._stop_requested,
                            on_process=self._on_process,
                        )
                        normalized_audio_clips.append(out_audio)
                        self._emit_job_progress(idx, step_counter, total_steps)
                        step_counter += 1

                    self._check_stop()
                    self.status_signal.emit(
                        f"Output {idx}/{self.settings.render_count} · Concat video ({len(normalized_video_clips)} parts)"
                    )
                    video_concat = temp_dir / "video_concat.mp4"
                    concat_video_files(
                        normalized_video_clips,
                        video_concat,
                        log_fn=self._log,
                        should_stop=lambda: self._stop_requested,
                        on_process=self._on_process,
                    )
                    self._emit_job_progress(idx, step_counter, total_steps)
                    step_counter += 1

                    self._check_stop()
                    self.status_signal.emit(
                        f"Output {idx}/{self.settings.render_count} · Concat audio ({len(normalized_audio_clips)} parts)"
                    )
                    audio_concat = temp_dir / "audio_concat.m4a"
                    concat_audio_files(
                        normalized_audio_clips,
                        audio_concat,
                        log_fn=self._log,
                        should_stop=lambda: self._stop_requested,
                        on_process=self._on_process,
                    )
                    self._emit_job_progress(idx, step_counter, total_steps)
                    step_counter += 1

                    self._check_stop()
                    self.status_signal.emit(f"Output {idx}/{self.settings.render_count} · Trim video")
                    target_duration = plan.job.target_duration_seconds
                    video_trim = temp_dir / "video_trim.mp4"
                    audio_trim = temp_dir / "audio_trim.m4a"
                    trim_media(
                        video_concat,
                        video_trim,
                        target_duration,
                        log_fn=self._log,
                        should_stop=lambda: self._stop_requested,
                        on_process=self._on_process,
                    )
                    self._emit_job_progress(idx, step_counter, total_steps)
                    step_counter += 1

                    self.status_signal.emit(f"Output {idx}/{self.settings.render_count} · Trim audio")
                    trim_media(
                        audio_concat,
                        audio_trim,
                        target_duration,
                        log_fn=self._log,
                        should_stop=lambda: self._stop_requested,
                        on_process=self._on_process,
                    )
                    self._emit_job_progress(idx, step_counter, total_steps)
                    step_counter += 1

                    self._check_stop()
                    self.status_signal.emit(f"Output {idx}/{self.settings.render_count} · Mux final MP4")
                    mux_video_audio(
                        video_file=video_trim,
                        audio_file=audio_trim,
                        output_file=plan.job.output_path,
                        duration_seconds=target_duration,
                        log_fn=self._log,
                        should_stop=lambda: self._stop_requested,
                        on_process=self._on_process,
                    )
                    self._emit_job_progress(idx, step_counter, total_steps)
                    step_counter += 1
                    self._log(f"Completed: {plan.job.output_path.name}")

                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)

            self.progress_signal.emit(100)
            self.status_signal.emit("All renders completed.")
            self.finished_signal.emit()
        except InterruptedError:
            self.status_signal.emit("Render stopped.")
            self._log("render stopped by user")
            self.finished_signal.emit()
        except Exception as ex:
            self.status_signal.emit("Render failed.")
            self.error_signal.emit(str(ex))
            self._log(traceback.format_exc())
            self.finished_signal.emit()
