import subprocess
import shutil
import traceback
from queue import Queue, Empty
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .ffmpeg_utils import (
    FFmpegError,
    VideoEncodeProfile,
    build_encoder_try_chain,
    concat_audio_files,
    concat_video_files,
    create_reverse_clip,
    ffmpeg_exists,
    ffprobe_exists,
    mux_video_audio,
    normalize_audio_clip,
    normalize_video_clip,
    probe_available_video_encoders,
    probe_media,
    trim_media,
)
from .models import AppSettings
from .render_planner import build_render_plan, create_render_job


class RenderWorker(QThread):
    log_signal = Signal(str)
    status_signal = Signal(str)
    """Legacy / global phases (FFmpeg check, errors); UI merges via coordinator."""
    step_progress_signal = Signal(str, int, int, int)
    """role, output_index, steps_done (0..total), total_steps — for global progress bar."""
    activity_signal = Signal(str, str)
    """role, short line for status (e.g. Encode 2/10 · file.mp4)."""
    output_done_signal = Signal(int, str)
    """output_index, worker_role — clears this worker from merged progress."""
    finished_signal = Signal()
    error_signal = Signal(str)

    def __init__(
        self,
        settings: AppSettings,
        mp4_files: list[Path],
        mp3_files: list[Path],
        job_queue: Queue[int] | None = None,
        worker_role: str = "GPU",
    ) -> None:
        super().__init__()
        self.settings = settings
        self.mp4_files = mp4_files
        self.mp3_files = mp3_files
        self._stop_requested = False
        self._current_process: subprocess.Popen[str] | None = None
        self._encode_profile: VideoEncodeProfile | None = None
        self._encoder_try_chain: list[VideoEncodeProfile] = []
        self.worker_role = worker_role
        self.total_jobs = int(settings.render_count)

        if job_queue is None:
            self.job_queue: Queue[int] = Queue()
            for idx in range(1, self.total_jobs + 1):
                self.job_queue.put(idx)
            self._owns_queue = True
        else:
            self.job_queue = job_queue
            self._owns_queue = False

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

    def _emit_step_progress(self, output_idx: int, steps_done: int, total_steps: int) -> None:
        """Report substeps for coordinator to merge across parallel GPU workers."""
        if total_steps <= 0:
            return
        self.step_progress_signal.emit(self.worker_role, output_idx, steps_done, total_steps)

    def _video_encode_call(self, fn: Callable[..., None], *args, **kwargs) -> None:
        """
        Run FFmpeg encode. First time in this output: try each GPU encoder in order, then CPU.
        After one success, lock encoder for the rest of this output file.
        """
        if self._encode_profile is not None:
            kwargs["profile"] = self._encode_profile
            fn(*args, **kwargs)
            return

        last_error: FFmpegError | None = None
        for profile in self._encoder_try_chain:
            kwargs["profile"] = profile
            try:
                fn(*args, **kwargs)
                self._encode_profile = profile
                self._log(f"Encoder locked to: {profile.display_name}")
                return
            except InterruptedError:
                raise
            except FFmpegError as ex:
                last_error = ex
                self._log(f"{profile.display_name} failed — trying next encoder.")
                continue
        if last_error is not None:
            raise last_error
        raise FFmpegError("No encoder candidate available.")

    def run(self) -> None:
        try:
            self.activity_signal.emit(self.worker_role, "Đang kiểm tra FFmpeg…")
            if not ffmpeg_exists():
                raise FFmpegError("ffmpeg not found. Please install FFmpeg and add to PATH.")
            if not ffprobe_exists():
                raise FFmpegError("ffprobe not found. Please install FFmpeg and add to PATH.")

            video_info_map = {}
            audio_info_map = {}

            self.activity_signal.emit(self.worker_role, "Đang đọc metadata MP4/MP3…")
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

            available_encoders = probe_available_video_encoders()
            self._log(
                "FFmpeg video encoders (detected): "
                + (", ".join(sorted(available_encoders)) if available_encoders else "(none)")
            )

            output_root = Path(self.settings.output_folder).expanduser()
            output_root.mkdir(parents=True, exist_ok=True)

            while True:
                self._check_stop()
                try:
                    idx = self.job_queue.get_nowait()
                except Empty:
                    break

                self.activity_signal.emit(
                    self.worker_role, f"Bắt đầu output {idx}/{self.total_jobs}"
                )
                self._log(f"=== Start output {idx}/{self.total_jobs} ({self.worker_role}) ===")

                try:
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
                    self._log(
                        "Selected mp4: " + ", ".join(p.name for p in job.selected_mp4_files)
                    )
                    self._log(
                        "Selected mp3: " + ", ".join(p.name for p in job.selected_mp3_files)
                    )

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

                    self._encoder_try_chain = build_encoder_try_chain(
                        available_encoders,
                        self.settings.video_encoder,
                    )
                    self._encode_profile = None
                    self._log(
                        "Encoder try order (first success locks for this output): "
                        + " → ".join(p.display_name for p in self._encoder_try_chain)
                    )
                    self._emit_step_progress(idx, 0, total_steps)

                    try:
                        v_total = len(video_timeline)
                        a_total = len(audio_timeline)
                        for clip_idx, timeline_entry in enumerate(video_timeline, start=1):
                            self._check_stop()
                            is_reverse = timeline_entry.startswith("__REVERSE__|")
                            src = Path(timeline_entry.replace("__REVERSE__|", ""))
                            base_name = f"v_{clip_idx:04d}_{src.stem}"
                            if is_reverse:
                                self.activity_signal.emit(
                                    self.worker_role,
                                    f"Output #{idx} · Reverse {clip_idx}/{v_total} · {src.name}",
                                )
                                self._log(f"Creating reverse clip for: {src.name}")
                                reversed_clip = temp_dir / f"{base_name}_rev_raw.mp4"
                                self._video_encode_call(
                                    create_reverse_clip,
                                    src,
                                    reversed_clip,
                                    log_fn=self._log,
                                    should_stop=lambda: self._stop_requested,
                                    on_process=self._on_process,
                                )
                                source_for_normalize = reversed_clip
                            else:
                                source_for_normalize = src
                            self.activity_signal.emit(
                                self.worker_role,
                                f"Output #{idx} · Encode {clip_idx}/{v_total} · {src.name}",
                            )
                            normalized = temp_dir / f"{base_name}_norm.mp4"
                            self._video_encode_call(
                                normalize_video_clip,
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
                            step_counter += 1
                            self._emit_step_progress(idx, step_counter, total_steps)

                        for audio_idx, audio_src in enumerate(audio_timeline, start=1):
                            self._check_stop()
                            self.activity_signal.emit(
                                self.worker_role,
                                f"Output #{idx} · Audio {audio_idx}/{a_total} · {audio_src.name}",
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
                            step_counter += 1
                            self._emit_step_progress(idx, step_counter, total_steps)

                        self._check_stop()
                        self.activity_signal.emit(
                            self.worker_role,
                            f"Output #{idx} · Nối video ({len(normalized_video_clips)} phần)",
                        )
                        video_concat = temp_dir / "video_concat.mp4"
                        concat_video_files(
                            normalized_video_clips,
                            video_concat,
                            log_fn=self._log,
                            should_stop=lambda: self._stop_requested,
                            on_process=self._on_process,
                        )
                        step_counter += 1
                        self._emit_step_progress(idx, step_counter, total_steps)

                        self._check_stop()
                        self.activity_signal.emit(
                            self.worker_role,
                            f"Output #{idx} · Nối nhạc ({len(normalized_audio_clips)} phần)",
                        )
                        audio_concat = temp_dir / "audio_concat.m4a"
                        concat_audio_files(
                            normalized_audio_clips,
                            audio_concat,
                            log_fn=self._log,
                            should_stop=lambda: self._stop_requested,
                            on_process=self._on_process,
                        )
                        step_counter += 1
                        self._emit_step_progress(idx, step_counter, total_steps)

                        self._check_stop()
                        self.activity_signal.emit(self.worker_role, f"Output #{idx} · Cắt video")
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
                        step_counter += 1
                        self._emit_step_progress(idx, step_counter, total_steps)

                        self.activity_signal.emit(self.worker_role, f"Output #{idx} · Cắt nhạc")
                        trim_media(
                            audio_concat,
                            audio_trim,
                            target_duration,
                            log_fn=self._log,
                            should_stop=lambda: self._stop_requested,
                            on_process=self._on_process,
                        )
                        step_counter += 1
                        self._emit_step_progress(idx, step_counter, total_steps)

                        self._check_stop()
                        self.activity_signal.emit(self.worker_role, f"Output #{idx} · Ghép MP4 cuối")
                        self._video_encode_call(
                            mux_video_audio,
                            video_file=video_trim,
                            audio_file=audio_trim,
                            output_file=plan.job.output_path,
                            duration_seconds=target_duration,
                            log_fn=self._log,
                            should_stop=lambda: self._stop_requested,
                            on_process=self._on_process,
                        )
                        step_counter += 1
                        self._emit_step_progress(idx, step_counter, total_steps)
                        self._log(f"Completed: {plan.job.output_path.name}")
                        self.output_done_signal.emit(idx, self.worker_role)

                    except Exception:
                        # Put the failed output back to queue so the other GPU (or CPU) can retry.
                        if not self._stop_requested:
                            self.job_queue.put(idx)
                        raise

                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)

                except Exception:
                    # Outer try for this output: the inner block already re-queues on failure.
                    raise

            self.activity_signal.emit(self.worker_role, "Hàng đợi worker đã xử lý xong.")
            self.finished_signal.emit()
        except InterruptedError:
            self.status_signal.emit("Render stopped.")
            self._log(f"[{self.worker_role}] render stopped by user")
            self.finished_signal.emit()
        except Exception as ex:
            self.status_signal.emit("Render failed (worker).")
            self.error_signal.emit(f"[{self.worker_role}] {ex}")
            self._log(traceback.format_exc())
            self.finished_signal.emit()
