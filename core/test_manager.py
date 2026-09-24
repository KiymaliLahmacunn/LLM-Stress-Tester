"""
core/test_manager.py
--------------------
Manages test lifecycle: configuration, execution, timing, and export.
Thread-safe with proper locking and race-condition guards.
"""

import csv
import datetime as _dt
import os
import threading
import time
from collections import deque

from core.excel_exporter import finalize_and_format_excel
from core.utils import detect_hardware
from core.worker import CSV_HEADERS, worker_loop

# Maximum number of log lines kept in memory for the UI terminal
_MAX_LOG_LINES = 500


class TestManager:
    def __init__(self):
        # --- Locks ---
        self.csv_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self._stop_lock = threading.Lock()  # prevents double stop_test

        # --- Logs (deque for O(1) rotation) ---
        self.logs: deque[str] = deque(maxlen=_MAX_LOG_LINES)

        # --- Worker tracking ---
        self.workers: list[threading.Thread] = []
        self.task_counter = 0

        # --- File paths ---
        self._base_dir = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        )
        self.csv_filename = os.path.join(self._base_dir, "test_results.csv")
        self.debug_log_path = os.path.join(self._base_dir, "debug.log")

        # --- Test config (set by configure_test) ---
        self.selected_model_name: str | None = None
        self.selected_model_url: str | None = None
        self.active_dataset_path: str | None = None
        self.active_dataset: list[dict] = []  # List of {"id": str, "q": str}
        self.concurrency = 1
        self.rps = 1
        self.duration = 60
        self.thinking_enabled = False
        self.streaming_enabled = False

        # --- Runtime state ---
        self.is_testing = False
        self.test_start_time = 0.0
        self.test_end_time = 0.0
        self._export_base = "TestResult"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure_test(
        self,
        model_name: str,
        model_url: str,
        dataset_path: str,
        concurrency: int,
        rps: int,
        duration: int,
        thinking: bool,
        streaming: bool,
        custom_hardware: str = "",
        seed: int | None = None,
        timeout: int = 30,
    ):
        self.selected_model_name = model_name
        self.selected_model_url = model_url
        self.active_dataset_path = dataset_path
        self.concurrency = max(1, concurrency)
        self.rps = max(1, rps)
        self.duration = max(1, duration)
        self.thinking_enabled = thinking
        self.streaming_enabled = streaming
        self.custom_hardware = custom_hardware
        self.seed = seed
        self.timeout = max(1, timeout)

        # Load the dataset into memory
        self.active_dataset = []
        if dataset_path and os.path.exists(dataset_path):
            with open(dataset_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f, delimiter=";")
                first_row = True
                for row in reader:
                    if first_row:
                        first_row = False
                        continue  # Skip header
                    if len(row) >= 2:
                        self.active_dataset.append(
                            {"id": str(row[0]).strip(), "q": str(row[1]).strip()}
                        )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def add_log(self, message: str):
        """Thread-safe append to the in-memory log buffer."""
        with self.log_lock:
            self.logs.append(message)

    def get_logs_snapshot(self) -> list[str]:
        """Return a thread-safe copy of current logs."""
        with self.log_lock:
            return list(self.logs)

    # ------------------------------------------------------------------
    # Test lifecycle
    # ------------------------------------------------------------------

    def start_test(self):
        with self._stop_lock:
            if self.is_testing:
                return
            if not self.selected_model_url or not self.active_dataset:
                return
            self.is_testing = True

        if self.seed is None:
            import random

            self.seed = random.randint(1, 2147483647)

        import random

        self.rng = random.Random(self.seed)
        self.rng_lock = threading.Lock()
        self.task_assigned_counter = 0

        dataset_name = os.path.basename(self.active_dataset_path or "Dataset")

        with self.log_lock:
            self.logs.clear()
            self.logs.append(
                f"[{_dt.datetime.now().strftime('%H:%M:%S')}] Test Sequence Initiated."
            )
            self.logs.append(f"Model: {self.selected_model_name}")
            self.logs.append(f"Target: {self.selected_model_url}")
            self.logs.append(
                f"Dataset: {dataset_name} ({len(self.active_dataset)} questions)"
            )
            self.logs.append(
                f"Config: concurrency={self.concurrency}, rps={self.rps}, duration={self.duration}s, seed={self.seed}"
            )

        # --- Build file names ---
        raw = self.selected_model_name or "Unknown"
        safe = "".join(c if c.isalnum() or c in ".-" else "_" for c in raw).strip("_")
        safe = (safe[:1].upper() + safe[1:]) if safe else "Model"
        safe = safe[:50]

        ds_raw = dataset_name.replace(".csv", "")
        safe_ds = "".join(c if c.isalnum() or c in ".-" else "_" for c in ds_raw).strip(
            "_"
        )[:15]

        abbr_think = "T" if self.thinking_enabled else "NT"
        abbr_stream = "S" if self.streaming_enabled else "NS"
        ts = _dt.datetime.now().strftime("%d.%m.%Y_%H-%M")

        self._export_base = f"{safe}_{safe_ds}_{abbr_think}_{abbr_stream}_{ts}"

        # Create unique directory for this test run
        results_dir = os.path.join(self._base_dir, "results", self._export_base)
        os.makedirs(results_dir, exist_ok=True)
        self._current_run_dir = results_dir

        self.csv_filename = os.path.join(
            results_dir, f"{self._export_base}_RUNNING.csv"
        )
        self.debug_log_path = os.path.join(
            results_dir, f"{self._export_base}_RUNNING.log"
        )
        self.task_counter = 0

        # --- Live Stats Tracking ---
        self.stat_success = 0
        self.stat_failed = 0
        self.stat_tokens = 0
        self.stat_ttft = []
        self.stat_gen = []
        self.stat_total = []

        self.workers = []

        # Initialize fresh files
        with open(self.debug_log_path, "w", encoding="utf-8") as lf:
            lf.write(f"=== Test Log: {self._export_base} ===\n")
            lf.write(f"Started: {_dt.datetime.now().isoformat()}\n\n")

        with open(self.csv_filename, mode="w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f, delimiter=";").writerow(CSV_HEADERS)

        # --- Timing ---
        delay = self.concurrency / self.rps if self.rps > 0 else 0.0
        self.test_start_time = time.time()
        self.test_end_time = self.test_start_time + self.duration

        # --- Launch threads ---
        threading.Thread(target=self._check_timer, daemon=True).start()

        for i in range(self.concurrency):
            t = threading.Thread(
                target=worker_loop,
                args=(self, delay, self.thinking_enabled, self.streaming_enabled),
                name=f"Worker-{i:02d}",
                daemon=True,
            )
            self.workers.append(t)
            t.start()

    def stop_test(self):
        """
        Stop the test and trigger export.
        Uses a lock to prevent double-invocation from timer + manual stop.
        """
        with self._stop_lock:
            if not self.is_testing:
                return
            self.is_testing = False

        self.add_log(
            f"\n[{_dt.datetime.now().strftime('%H:%M:%S')}] Test Sequence Complete. Exporting results..."
        )
        threading.Thread(target=self._export_excel, daemon=True).start()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_timer(self):
        while self.is_testing:
            if time.time() >= self.test_end_time:
                self.stop_test()
                return
            time.sleep(0.5)

    def _export_excel(self):
        # Wait for workers to finish their last in-flight request
        for t in self.workers:
            if t.is_alive():
                t.join(timeout=15.0)

        with self.csv_lock:
            hardware_info = getattr(self, "custom_hardware", "")
            if not hardware_info:
                hardware_info, _ = detect_hardware()
            thinking_str = "Thinking" if self.thinking_enabled else "NoThink"
            streaming_str = "Enabled" if self.streaming_enabled else "Disabled"

            dataset_name = os.path.basename(self.active_dataset_path or "Unknown")

            end_ts = _dt.datetime.now().strftime("%d.%m.%Y %H-%M")
            final_name = f"{self._export_base}"
            xlsx_path = os.path.join(self._current_run_dir, f"{final_name}.xlsx")

            result = finalize_and_format_excel(
                csv_path=self.csv_filename,
                xlsx_path=xlsx_path,
                model_name=self.selected_model_name or "Unknown",
                task_level=dataset_name,
                thinking_str=thinking_str,
                streaming_str=streaming_str,
                hardware_info=hardware_info,
            )

            # Save metadata as JSON for UI
            import json as _json

            meta_path = os.path.join(self._current_run_dir, f"{final_name}.json")
            try:
                with open(meta_path, "w", encoding="utf-8") as mf:
                    _json.dump(
                        {
                            "model": self.selected_model_name or "Unknown",
                            "hardware": hardware_info,
                            "dataset": dataset_name,
                            "thinking": "On" if self.thinking_enabled else "Off",
                            "streaming": "On" if self.streaming_enabled else "Off",
                            "filename": f"{final_name}.xlsx",
                            "seed": self.seed,
                        },
                        mf,
                        ensure_ascii=False,
                        indent=2,
                    )
            except Exception as e:
                print("Could not save metadata JSON:", e)

            if result:
                self.add_log(
                    f"[EXPORT] Excel report saved: {os.path.basename(xlsx_path)}"
                )
            else:
                self.add_log("[EXPORT] ERROR: Failed to generate Excel report!")

            # Rename running log to final log
            final_log = os.path.join(self._current_run_dir, f"{final_name}.log")
            if os.path.exists(self.debug_log_path):
                try:
                    # Append footer before renaming
                    with open(self.debug_log_path, "a", encoding="utf-8") as lf:
                        lf.write(
                            f"\n=== Test Finished: {_dt.datetime.now().isoformat()} ===\n"
                        )
                        lf.write(f"Total requests: {self.task_counter}\n")
                    os.rename(self.debug_log_path, final_log)
                    self.add_log(
                        f"[EXPORT] Debug log saved: {os.path.basename(final_log)}"
                    )
                except Exception as e:
                    self.add_log(f"[EXPORT] Log rename failed: {e}")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self, last_log_idx: int = 0) -> dict:
        if self.is_testing:
            elapsed = time.time() - self.test_start_time
            progress = (elapsed / self.duration) * 100 if self.duration > 0 else 0
            time_left = max(0.0, self.test_end_time - time.time())

            with self.log_lock:
                logs_snapshot = list(self.logs)[last_log_idx:]
                new_idx = len(self.logs)

                # Active workers = alive threads
                active = sum(1 for t in self.workers if t.is_alive())

                # Stats
                total = self.task_counter
                succ = self.stat_success
                fail = self.stat_failed

                avg_rps = total / elapsed if elapsed > 0 else 0
                avg_tps = self.stat_tokens / elapsed if elapsed > 0 else 0

                def _avg(lst):
                    return sum(lst) / len(lst) if lst else 0.0

                avg_ttft = _avg(self.stat_ttft)
                avg_gen = _avg(self.stat_gen)
                avg_tot = _avg(self.stat_total)

            return {
                "is_testing": True,
                "tasks_completed": total,
                "progress_percent": min(100.0, max(0.0, progress)),
                "time_remaining": time_left,
                "progress": min(1.0, progress / 100.0),
                "active_workers": active,
                "total_requests": total,
                "success_count": succ,
                "fail_count": fail,
                "avg_rps": avg_rps,
                "avg_tps": avg_tps,
                "avg_ttft_ms": avg_ttft,
                "avg_generation_ms": avg_gen,
                "avg_total_ms": avg_tot,
                "new_logs": logs_snapshot,
                "last_log_idx": new_idx,
            }
        else:
            return {
                "is_testing": False,
                "tasks_completed": 0,
                "progress_percent": 0.0,
                "time_remaining": 0.0,
                "progress": 0.0,
                "active_workers": 0,
                "total_requests": 0,
                "success_count": 0,
                "fail_count": 0,
                "avg_rps": 0.0,
                "avg_tps": 0.0,
                "avg_ttft_ms": 0.0,
                "avg_generation_ms": 0.0,
                "avg_total_ms": 0.0,
                "new_logs": [],
                "last_log_idx": last_log_idx,
            }
