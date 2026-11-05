"""Module-level task functions for sandbox tests (spawned children import them by name)."""

import ctypes
import os
import struct
import threading
import time
from multiprocessing.connection import Connection

from tenderscreen.documents.sandbox import MAX_MESSAGE_BYTES, Json, send_json


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


def stall_mid_message(conn: Connection) -> None:
    """A child target that starts a 100-byte message, sends 10 bytes, and stalls."""
    os.write(conn.fileno(), struct.pack("!i", 100) + b"0123456789")
    time.sleep(60)


def die_mid_message(conn: Connection) -> None:
    """A child target that starts a 100-byte message, sends 10 bytes, and exits."""
    os.write(conn.fileno(), struct.pack("!i", 100) + b"0123456789")
    os._exit(5)


def report_environment(conn: Connection) -> None:
    """A child target that reports which environment variables it can see."""
    names: list[Json] = [*sorted(os.environ)]
    send_json(conn, names)
