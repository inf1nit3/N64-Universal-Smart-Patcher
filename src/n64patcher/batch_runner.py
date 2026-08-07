"""
batch_runner.py
Multi-threaded batch patching engine.
"""
import queue
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import n64_core as core

_STOP = object()


class LogPump:
    """Serializes log lines from N workers onto one consumer thread.

    `log_func` is supplied by a GUI or CLI that never expected concurrent
    calls; funnelling through a queue means the sink sees one line at a
    time in a single thread, whatever the worker count.
    """

    def __init__(self, sink: Callable[[str], None]):
        self._sink = sink
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._drain, daemon=True,
                                        name="n64-log-pump")

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            try:
                self._sink(item)
            except Exception:
                # A failing log sink must never take the batch down.
                pass

    def __call__(self, msg: str = "") -> None:
        self._queue.put(msg)

    def __enter__(self) -> "LogPump":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._queue.put(_STOP)
        self._thread.join(timeout=5)


def batch_patch_roms(
    rom_paths: list[str],
    options: core.PatchOptions,
    max_workers: int = 4,
    log_func: Callable[[str], None] = print,
    output_dir: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Patches a list of ROM files in parallel using ThreadPoolExecutor.
    Every result dict includes the original 'input' path. Cancelled ROMs
    are counted as skipped, not as errors.

    *should_cancel* is polled by each worker, so a batch stops at the next
    stage boundary instead of running to completion. Ctrl+C does the same
    and returns the partial summary with 'cancelled' set, rather than
    blocking until every queued ROM has been processed.

    Returns summary dict with results list and stats.
    """
    results_list = []
    patched_count = 0
    skipped_count = 0
    error_count = 0

    stop = threading.Event()

    def cancelled() -> bool:
        return stop.is_set() or bool(should_cancel and should_cancel())

    with LogPump(log_func) as pump, ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_rom = {
            executor.submit(core.patch_rom, rom, options, pump,
                            cancelled, output_dir): rom
            for rom in rom_paths
        }

        try:
            for future in as_completed(future_to_rom):
                rom = future_to_rom[future]
                try:
                    res = future.result()
                    results_list.append(res)
                    status = res.get("status")
                    if status == "patched":
                        patched_count += 1
                    elif status in ("skipped", "cancelled"):
                        skipped_count += 1
                    else:
                        error_count += 1
                except Exception as e:
                    error_count += 1
                    results_list.append({
                        "status": "error",
                        "filename": rom,
                        "message": str(e),
                        "output": None,
                        "input": rom,
                        "applied": set(),
                    })
        except KeyboardInterrupt:
            # Tell running workers to wind down and drop whatever has
            # not started yet, so the executor's shutdown is quick.
            stop.set()
            for future in future_to_rom:
                future.cancel()

    # Preserve the caller's input order for stable reports
    order = {rom: i for i, rom in enumerate(rom_paths)}
    results_list.sort(key=lambda r: order.get(r.get("input"), 0))

    return {
        "patched": patched_count,
        "skipped": skipped_count,
        "errors": error_count,
        "cancelled": cancelled(),
        "results": results_list,
    }
