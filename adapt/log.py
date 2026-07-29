"""Progress logging.

The pipeline does a lot of expensive, invisible work — opening a 100 MB PSD,
compositing layers, building saliency maps, rendering supersampled canvases. When
something is slow or a preview does not appear, the only way to know *which* step
is responsible is to have it say so. Everything is printed on one line per step,
with its duration, so the log reads as a timeline.

Set ``ADAPT_QUIET=1`` (or call :func:`quiet`) to silence it.
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager

_enabled = os.environ.get("ADAPT_QUIET", "") not in ("1", "true", "yes")
_t0 = time.time()


def quiet(off: bool = True):
    """Turn logging off (or back on)."""
    global _enabled
    _enabled = not off


def enabled() -> bool:
    return _enabled


def rss_mb() -> float:
    """Resident memory of this process, in MB — 0.0 if it cannot be read.

    Worth printing on every step: the usual reason this tool makes a laptop
    crawl is not CPU but a working set large enough to force swapping.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        c = _Counters()
        c.cb = ctypes.sizeof(c)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        # Modern Windows exports this from kernel32; older ones only from psapi.
        for dll, fn in ((ctypes.windll.kernel32, "K32GetProcessMemoryInfo"),
                        (ctypes.WinDLL("psapi"), "GetProcessMemoryInfo")):
            call = getattr(dll, fn, None)
            if not call:
                continue
            # Without these the pseudo-handle (-1) is truncated and the call fails.
            call.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
            call.restype = wintypes.BOOL
            if call(handle, ctypes.byref(c), c.cb):
                return c.WorkingSetSize / 1024 ** 2
    except Exception:
        pass
    try:                                    # POSIX
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / (1024 if sys.platform == "darwin" else 1) / 1024
    except Exception:
        return 0.0


def log(msg: str, indent: int = 0):
    if _enabled:
        mem = rss_mb()
        tag = f"{mem:6.0f}MB" if mem else "       "
        print(f"[{time.time() - _t0:7.2f}s {tag}] {'  ' * indent}{msg}", flush=True,
              file=sys.stderr)


@contextmanager
def step(label: str, indent: int = 0):
    """Time a block and report it. The body can append detail via the yielded
    dict, e.g. ``with step('load') as s: s['note'] = '4 elements'``."""
    t = time.time()
    detail: dict = {}
    log(f"{label} ...", indent)
    try:
        yield detail
    finally:
        note = f"  {detail['note']}" if detail.get("note") else ""
        log(f"{label} done in {time.time() - t:.2f}s{note}", indent)


def mb(img) -> float:
    """Rough uncompressed size of a PIL image, in MB — the number that decides
    whether this machine is going to start swapping."""
    w, h = img.size
    return w * h * len(img.getbands()) / 1024 ** 2
