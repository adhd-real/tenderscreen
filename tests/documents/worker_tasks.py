"""Module-level task functions for sandbox tests (spawned children import them by name)."""

import ctypes
import os
import threading
import time

from tenderscreen.documents.sandbox import MAX_MESSAGE_BYTES, Json


def square(value: int) -> Json:
    return value * value


def fail(value: int) -> Json:
    raise ValueError(f"cannot handle {value}")


def sleep_and_record(seconds: float) -> Json:
    """Sleep, then report [worker pid, start time, end time]."""
    started = time.time()
    time.sleep(seconds)
    return [os.getpid(), started, time.time()]


def segfault(value: int) -> Json:
    ctypes.string_at(0)  # SIGSEGV, like a PDFium crash on a malicious page
    return value


_doomed = False


def die_soon(value: int) -> Json:
    """On first use, doom this worker: it exits 0.3 s later, idle or mid-task."""
    global _doomed  # one timer per worker process
    if not _doomed:
        _doomed = True
        threading.Timer(0.3, os._exit, [3]).start()
    time.sleep(0.25)
    return value


def allocate(megabytes: int) -> Json:
    block = bytearray(megabytes * 1024 * 1024)
    return len(block)


def unserializable(value: int) -> Json:
    return {value}  # type: ignore[return-value]  # a set: deliberately not JSON


def oversized(value: int) -> Json:
    return "x" * (MAX_MESSAGE_BYTES + value)


def silent(conn: object) -> None:
    """A child target that never answers."""
    time.sleep(60)
