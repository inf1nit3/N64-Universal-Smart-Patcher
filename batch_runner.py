"""
batch_runner.py
Multi-threaded batch patching engine.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Dict, Any, Optional
import n64_core as core


def batch_patch_roms(
    rom_paths: List[str],
    options: core.PatchOptions,
    max_workers: int = 4,
    log_func: Callable[[str], None] = print,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Patches a list of ROM files in parallel using ThreadPoolExecutor.
    Every result dict includes the original 'input' path. Cancelled ROMs
    are counted as skipped, not as errors.
    Returns summary dict with results list and stats.
    """
    results_list = []
    patched_count = 0
    skipped_count = 0
    error_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_rom = {
            executor.submit(core.patch_rom, rom, options, log_func,
                            lambda: False, output_dir): rom
            for rom in rom_paths
        }

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

    # Preserve the caller's input order for stable reports
    order = {rom: i for i, rom in enumerate(rom_paths)}
    results_list.sort(key=lambda r: order.get(r.get("input"), 0))

    return {
        "patched": patched_count,
        "skipped": skipped_count,
        "errors": error_count,
        "results": results_list,
    }