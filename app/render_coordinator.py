from __future__ import annotations

from dataclasses import replace
from queue import Queue
from typing import Callable

from PySide6.QtCore import QObject

from .constants import (
    VIDEO_ENCODER_AUTO,
    VIDEO_ENCODER_CPU,
    VIDEO_ENCODER_NVENC,
    VIDEO_ENCODER_QSV,
)
from .ffmpeg_utils import probe_available_video_encoders
from .models import AppSettings
from .render_worker import RenderWorker


class RenderCoordinator(QObject):
    """
    Run up to 2 GPU workers in parallel sharing a single job queue.
    - If one GPU worker fails, the other keeps consuming the queue.
    - If all started GPU workers fail, a CPU worker is started to finish remaining jobs.
    """

    def __init__(
        self,
        settings: AppSettings,
        mp4_files: list,
        mp3_files: list,
        log_cb: Callable[[str], None],
        status_cb: Callable[[str], None],
        progress_cb: Callable[[int], None],
        on_finished: Callable[[], None],
        temp_role_prefix: str = "",
    ) -> None:
        super().__init__()
        self.settings = settings
        self.mp4_files = mp4_files
        self.mp3_files = mp3_files
        self.log_cb = log_cb
        self.status_cb = status_cb
        self.progress_cb = progress_cb
        self.on_finished = on_finished
        self.temp_role_prefix = temp_role_prefix

        self.total_jobs = int(settings.render_count)
        self.job_queue: Queue[int] = Queue()
        for idx in range(1, self.total_jobs + 1):
            self.job_queue.put(idx)

        self.completed_outputs = 0

        self._workers: list[RenderWorker] = []
        self._gpu_roles_started: set[str] = set()
        self._gpu_roles_failed: set[str] = set()
        self._cpu_started = False
        self._stopping = False
        self._finished_called = False

        # Merged UI: progress across parallel workers + per-role activity line
        self._partial_steps: dict[str, tuple[int, int, int]] = {}
        """role -> (output_index, steps_done, total_steps)."""
        self._activity: dict[str, str] = {}

        available_encoders = probe_available_video_encoders()
        self._gpu_roles_started = self._decide_gpu_roles(available_encoders, settings.video_encoder)

    def _decide_gpu_roles(self, available_encoders: set[str], preference: str) -> set[str]:
        pref = (preference or VIDEO_ENCODER_AUTO).strip().lower()
        roles: set[str] = set()

        if pref == VIDEO_ENCODER_AUTO:
            if "h264_qsv" in available_encoders:
                roles.add(VIDEO_ENCODER_QSV)
            if "h264_nvenc" in available_encoders:
                roles.add(VIDEO_ENCODER_NVENC)
            return roles

        if pref == VIDEO_ENCODER_QSV and "h264_qsv" in available_encoders:
            roles.add(VIDEO_ENCODER_QSV)
            return roles

        if pref == VIDEO_ENCODER_NVENC and "h264_nvenc" in available_encoders:
            roles.add(VIDEO_ENCODER_NVENC)
            return roles

        # cpu-only mode or unavailable gpu
        return roles

    def _make_worker(self, role: str) -> RenderWorker:
        if role == VIDEO_ENCODER_QSV:
            worker_settings = replace(self.settings, video_encoder=VIDEO_ENCODER_QSV)
            worker_role = "QSV"
        elif role == VIDEO_ENCODER_NVENC:
            worker_settings = replace(self.settings, video_encoder=VIDEO_ENCODER_NVENC)
            worker_role = "NVENC"
        else:
            worker_settings = replace(self.settings, video_encoder=VIDEO_ENCODER_CPU)
            worker_role = "CPU"

        return RenderWorker(
            settings=worker_settings,
            mp4_files=self.mp4_files,
            mp3_files=self.mp3_files,
            job_queue=self.job_queue,
            worker_role=worker_role,
        )

    def start(self) -> None:
        self.progress_cb(0)
        self.status_cb("Đang khởi động render…")
        self.log_cb("Dual render coordinator started.")

        if not self._gpu_roles_started:
            self._start_cpu_worker()
            return

        # Start 1 or 2 GPU workers depending on availability.
        for role in sorted(self._gpu_roles_started):
            self._start_gpu_worker(role)

    def _start_gpu_worker(self, role: str) -> None:
        worker = self._make_worker(role)
        self._workers.append(worker)

        self.log_cb(f"Starting worker: {worker.worker_role}")
        worker.log_signal.connect(lambda txt, r=worker.worker_role: self.log_cb(f"[{r}] {txt}"))
        worker.status_signal.connect(lambda msg, r=worker.worker_role: self.log_cb(f"[{r}] {msg}"))
        worker.step_progress_signal.connect(self._on_step_progress)
        worker.activity_signal.connect(self._on_activity)
        worker.output_done_signal.connect(self._on_output_done)
        worker.error_signal.connect(lambda msg, r=worker.worker_role: self._on_worker_error(r, msg))
        worker.finished_signal.connect(lambda r=worker.worker_role: self._on_worker_finished(r))
        worker.start()

    def _start_cpu_worker(self) -> None:
        if self._cpu_started or self._stopping:
            return
        if self.job_queue.empty():
            return

        worker = self._make_worker(VIDEO_ENCODER_CPU)
        self._cpu_started = True
        self._workers.append(worker)

        self.log_cb("Starting CPU worker as fallback...")
        worker.log_signal.connect(lambda txt, r=worker.worker_role: self.log_cb(f"[{r}] {txt}"))
        worker.status_signal.connect(lambda msg, r=worker.worker_role: self.log_cb(f"[{r}] {msg}"))
        worker.step_progress_signal.connect(self._on_step_progress)
        worker.activity_signal.connect(self._on_activity)
        worker.output_done_signal.connect(self._on_output_done)
        worker.error_signal.connect(lambda msg, r=worker.worker_role: self._on_worker_error(r, msg))
        worker.finished_signal.connect(lambda r=worker.worker_role: self._on_worker_finished(r))
        worker.start()

    def _on_step_progress(self, role: str, output_idx: int, steps_done: int, total_steps: int) -> None:
        if self._stopping:
            return
        self._partial_steps[role] = (output_idx, max(0, steps_done), max(1, total_steps))
        self._refresh_progress_and_status()

    def _on_activity(self, role: str, text: str) -> None:
        if self._stopping:
            return
        self._activity[role] = text
        self._refresh_progress_and_status()

    def _refresh_progress_and_status(self) -> None:
        total = max(1, self.total_jobs)
        in_flight = 0.0
        for _r, (_idx, s_done, s_tot) in self._partial_steps.items():
            in_flight += min(1.0, float(s_done) / float(s_tot))
        combined = self.completed_outputs + in_flight
        pct = int(100.0 * combined / float(total))
        pct = max(0, min(99, pct))
        if self.completed_outputs >= self.total_jobs:
            pct = 100
        self.progress_cb(pct)

        done = self.completed_outputs
        parts: list[str] = [f"Tổng: {done}/{self.total_jobs} file (~{pct}%)"]
        for role in sorted(self._activity.keys()):
            line = self._activity[role]
            if role in self._partial_steps:
                _oid, sd, st = self._partial_steps[role]
                parts.append(f"{role} · output #{_oid} · bước {sd}/{st} · {line}")
            else:
                parts.append(f"{role} · {line}")
        self.status_cb("  |  ".join(parts))

    def _on_output_done(self, _idx: int, role: str) -> None:
        self._partial_steps.pop(role, None)
        self._activity.pop(role, None)
        self.completed_outputs += 1
        self._refresh_progress_and_status()
        if self.completed_outputs >= self.total_jobs:
            self.status_cb(f"Hoàn thành {self.completed_outputs}/{self.total_jobs} file.")
            self.log_cb("All renders completed (coordinator).")
            self._stopping = True
            if not self._finished_called:
                self._finished_called = True
                self.on_finished()

    def _on_worker_error(self, worker_role: str, message: str) -> None:
        if self._stopping:
            return

        # Map worker_role string -> role id used in _gpu_roles_started
        role_id = None
        if worker_role.upper().startswith("QSV"):
            role_id = VIDEO_ENCODER_QSV
        elif worker_role.upper().startswith("NVENC"):
            role_id = VIDEO_ENCODER_NVENC

        if role_id and role_id in self._gpu_roles_started:
            self._gpu_roles_failed.add(role_id)
            self.log_cb(f"Worker failed: {worker_role}. {message}")

            if self._gpu_roles_failed.issuperset(self._gpu_roles_started):
                # Both GPU workers failed (or the only one started failed).
                self._start_cpu_worker()
        else:
            self.log_cb(f"Worker error ({worker_role}): {message}")

    def _on_worker_finished(self, worker_role: str) -> None:
        # If all outputs are done, _on_output_done will already finish.
        if self.completed_outputs >= self.total_jobs:
            return

        # If queue drained but not all outputs marked done, we still wait for other workers.
        if self.job_queue.empty() and not self._cpu_started:
            self._partial_steps.pop(worker_role, None)
            self._activity.pop(worker_role, None)
            self._refresh_progress_and_status()
            self.status_cb("Đang chờ worker khác / CPU fallback…")

    def stop(self) -> None:
        self._stopping = True
        self.status_cb("Stopping render...")
        self.log_cb("Render stopped by user (coordinator).")
        for w in self._workers:
            if w.isRunning():
                w.stop()
        if not self._finished_called:
            self._finished_called = True
            self.on_finished()

