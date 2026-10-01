import asyncio
import csv
import datetime as _dt
import os
import random
import threading
import time

import httpx

from core.excel_exporter import finalize_and_format_excel
from core.utils import detect_hardware
from core.worker import async_worker_task

CSV_HEADERS = [
    "Task ID",
    "Dataset ID",
    "Model",
    "Sent At (epoch ms)",
    "First Token At (epoch ms)",
    "Finished At (epoch ms)",
    "Status",
    "TTFT (ms)",
    "Generation Time (ms)",
    "Total Duration (ms)",
    "TPS",
    "Prompt Tokens",
    "Completion Tokens",
    "Total Tokens",
    "Reasoning Tokens",
    "Cached Tokens",
]


class TestManager:
    def __init__(self):
        self.is_testing = False
        self.in_warmup_phase = False
        self.warmup_duration = 3.0  # 3 seconds warmup

        self.selected_model_name: str | None = None
        self.selected_model_url: str | None = None
        self.active_dataset_path: str | None = None
        self.active_dataset: list[dict] = []
        self.concurrency = 1
        self.rps = 1
        self.duration = 60
        self.timeout = 30
        self.seed = None
        self.thinking_enabled = False
        self.streaming_enabled = True

        self.task_assigned_counter = 0
        self.task_counter = 0

        self.logs = []
        self.max_logs = 1000

        self.csv_filename = ""
        self.debug_log_path = ""
        self.csv_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self.rng_lock = threading.Lock()

        self.stat_success = 0
        self.stat_failed = 0
        self.stat_tokens = 0
        self.stat_ttft = []
        self.stat_gen = []
        self.stat_total = []

        self.test_start_time = 0.0
        self.test_end_time = 0.0

        self.active_async_tasks = 0
        self.async_loop_thread = None
        self.loop = None

        self.rng = random.Random()

    def add_log(self, msg: str):
        with self.log_lock:
            self.logs.append(msg)
            if len(self.logs) > self.max_logs:
                self.logs = self.logs[-self.max_logs :]

    def configure_test(
        self,
        provider: str,
        model_name: str,
        model_url: str,
        dataset_path: str,
        concurrency: int,
        rps: int,
        duration: int,
        thinking: bool,
        streaming: bool,
        hardware: str,
        seed: int | None,
        timeout: int,
    ):
        self.provider = provider
        self.selected_model_name = model_name
        self.selected_model_url = model_url
        self.active_dataset_path = dataset_path
        self.concurrency = max(1, concurrency)
        self.rps = max(1, rps)
        self.duration = max(1, duration)
        self.timeout = max(5, timeout)
        self.seed = seed
        self.thinking_enabled = thinking
        self.streaming_enabled = streaming
        self.custom_hardware = hardware

        self.active_dataset = []
        if dataset_path and os.path.exists(dataset_path):
            with open(dataset_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                for i, row in enumerate(reader):
                    if i == 0:
                        continue
                    if len(row) >= 2:
                        self.active_dataset.append(
                            {"id": str(row[0]).strip(), "q": str(row[1]).strip()}
                        )

    def start_test(self):
        with self._stop_lock:
            if self.is_testing:
                return
            if not self.selected_model_url or not self.active_dataset:
                return
            self.is_testing = True

        self.in_warmup_phase = True
        self.task_assigned_counter = 0
        self.task_counter = 0

        if self.seed is not None:
            self.rng.seed(self.seed)
        else:
            generated_seed = int(time.time() * 1000) % (2**32)
            self.seed = generated_seed
            self.rng.seed(self.seed)

        dataset_name = os.path.basename(self.active_dataset_path or "Dataset")

        with self.log_lock:
            self.logs.clear()
            self.logs.append(f"Target: {self.selected_model_url}")
            self.logs.append(
                f"Dataset: {dataset_name} ({len(self.active_dataset)} questions)"
            )
            self.logs.append(
                f"Config: concurrency={self.concurrency}, rps={self.rps}, duration={self.duration}s, seed={self.seed}"
            )
            self.logs.append("--- TEST STARTED ---")
            self.logs.append(
                f"[WARMUP] Starting {self.warmup_duration}s warmup phase. Traffic sent during warmup is excluded from final math report."
            )

        base_dir = os.path.dirname(os.path.abspath(__file__))
        results_dir = os.path.join(base_dir, "..", "results")
        os.makedirs(results_dir, exist_ok=True)
        self._current_run_dir = results_dir

        safe_model = "".join(
            c if c.isalnum() or c in ("-", "_") else "_"
            for c in (self.selected_model_name or "Unknown")
        )
        safe_model = safe_model[:50]
        timestamp = _dt.datetime.now().strftime("%d.%m.%Y_%H-%M")

        think_str = "T" if self.thinking_enabled else "NT"
        stream_str = "S" if self.streaming_enabled else "NS"
        self._export_base = (
            f"{safe_model}_{dataset_name}_{think_str}_{stream_str}_{timestamp}"
        )

        self.csv_filename = os.path.join(results_dir, f"{self._export_base}.csv")
        self.debug_log_path = os.path.join(
            results_dir, f"{self._export_base}_running.log"
        )

        self.stat_success = 0
        self.stat_failed = 0
        self.stat_tokens = 0
        self.stat_ttft = []
        self.stat_gen = []
        self.stat_total = []
        self.active_async_tasks = 0

        with open(self.debug_log_path, "w", encoding="utf-8") as lf:
            lf.write(f"=== Test Log: {self._export_base} ===\n")
            lf.write(f"Started: {_dt.datetime.now().isoformat()}\n\n")

        with open(self.csv_filename, mode="w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f, delimiter=";").writerow(CSV_HEADERS)

        self.test_start_time = time.time()
        self.test_end_time = self.test_start_time + self.duration + self.warmup_duration

        threading.Thread(target=self._check_timer, daemon=True).start()

        # Start the async event loop in a background thread
        self.async_loop_thread = threading.Thread(
            target=self._run_async_event_loop, daemon=True
        )
        self.async_loop_thread.start()

    def _run_async_event_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._async_test_runner())
        self.loop.close()

    async def _async_test_runner(self):
        api_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("API_KEY")
            or os.getenv("ANTHROPIC_API_KEY")
        )
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        delay = self.concurrency / self.rps if self.rps > 0 else 0.0

        limits = httpx.Limits(
            max_connections=self.concurrency + 10,
            max_keepalive_connections=self.concurrency,
        )
        async with httpx.AsyncClient(limits=limits, headers=headers) as client:
            tasks = []
            for i in range(self.concurrency):
                t = asyncio.create_task(self._tracked_async_worker(i, client, delay))
                tasks.append(t)

            await asyncio.gather(*tasks, return_exceptions=True)

    async def _tracked_async_worker(
        self, task_id: int, client: httpx.AsyncClient, delay: float
    ):
        self.active_async_tasks += 1
        try:
            await async_worker_task(
                self,
                task_id,
                client,
                delay,
                self.thinking_enabled,
                self.streaming_enabled,
            )
        finally:
            self.active_async_tasks -= 1

    def stop_test(self):
        with self._stop_lock:
            if not self.is_testing:
                return
            self.is_testing = False

        self.add_log(
            f"\n[{_dt.datetime.now().strftime('%H:%M:%S')}] Test Sequence Complete. Exporting results..."
        )
        threading.Thread(target=self._export_excel, daemon=True).start()

    def _check_timer(self):
        while self.is_testing:
            now = time.time()
            if (
                self.in_warmup_phase
                and now >= self.test_start_time + self.warmup_duration
            ):
                self.in_warmup_phase = False
                self.add_log(
                    "[WARMUP] Warmup complete. Now recording metrics for mathematical report."
                )

            if now >= self.test_end_time:
                self.stop_test()
                return
            time.sleep(0.5)

    def _export_excel(self):
        # Wait for async tasks to finish
        wait_start = time.time()
        while self.active_async_tasks > 0 and time.time() - wait_start < self.timeout + 2.0:
            time.sleep(0.5)

        with self.csv_lock:
            hardware_info = getattr(self, "custom_hardware", "")
            if not hardware_info:
                hardware_info, _ = detect_hardware()
            thinking_str = "Thinking" if self.thinking_enabled else "NoThink"
            streaming_str = "Enabled" if self.streaming_enabled else "Disabled"
            dataset_name = os.path.basename(self.active_dataset_path or "Unknown")
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

            final_log = os.path.join(self._current_run_dir, f"{final_name}.log")
            if os.path.exists(self.debug_log_path):
                try:
                    with open(self.debug_log_path, "a", encoding="utf-8") as lf:
                        lf.write(
                            f"\n=== Test Finished: {_dt.datetime.now().isoformat()} ===\n"
                        )
                    os.rename(self.debug_log_path, final_log)
                except Exception:
                    pass

    def get_status(self, last_log_idx: int = 0) -> dict:
        if self.is_testing:
            now = time.time()
            elapsed = now - self.test_start_time
            total_duration = self.duration + self.warmup_duration
            progress = (elapsed / total_duration) * 100 if total_duration > 0 else 0
            time_left = max(0.0, self.test_end_time - now)

            with self.log_lock:
                logs_snapshot = list(self.logs)[last_log_idx:]
                new_idx = len(self.logs)
                active = self.active_async_tasks
                succ = self.stat_success
                fail = self.stat_failed
                # If warmup is over, calculate RPS on post-warmup time
                effective_elapsed = (
                    max(0.1, elapsed - self.warmup_duration)
                    if not self.in_warmup_phase
                    else 0
                )
                total_recorded = succ + fail

                avg_rps = (
                    total_recorded / effective_elapsed if effective_elapsed > 0 else 0
                )
                avg_tps = (
                    self.stat_tokens / effective_elapsed if effective_elapsed > 0 else 0
                )

                def _avg(lst):
                    return sum(lst) / len(lst) if lst else 0.0

                avg_ttft = _avg(self.stat_ttft)
                avg_gen = _avg(self.stat_gen)
                avg_tot = _avg(self.stat_total)

            return {
                "is_testing": True,
                "tasks_completed": self.task_assigned_counter,
                "progress_percent": min(100.0, max(0.0, progress)),
                "time_remaining": time_left,
                "progress": min(1.0, progress / 100.0),
                "active_tasks": active,
                "total_requests": total_recorded,
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
                "active_tasks": 0,
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
