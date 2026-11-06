"""Run work on untrusted documents in child processes the parent fully controls.

Every child is a fresh "spawn" process with a memory cap (Linux ``RLIMIT_AS``,
which programs it starts, such as Tesseract, inherit), a scrubbed environment,
and the parent-death signal (it is killed if the parent dies). The parent waits
on the child's pipe and its process sentinel together, so it learns at once
when a child dies (a PDFium crash, the memory cap, the OOM killer) and can kill
exactly the child that overran its time limit; even reading a message is
bounded by a watchdog. ``multiprocessing.Pool`` is not used: it cannot tell a
dead worker from a slow one, and its shutdown can hang forever after a worker
is killed.

Children answer in size-capped JSON, never pickle: a child parses untrusted
documents, so whatever it sends back is treated as untrusted too (CLAUDE.md
security rules: never unpickle anything derived from a document).

This limits resources and the damage of a crash. It is not a privilege
boundary: a child runs as the same user, and native code in it could read
memory, files and the original environment block. For full isolation, run
document processing where no secrets are available.
"""

import ctypes
import json
import math
import multiprocessing
import os
import signal
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, MutableMapping, Sequence
from contextlib import suppress
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any, Final

# Largest message a child may send (one page's text and metadata). Decoding JSON
# costs about 20x its size in memory, so this keeps one answer near 100 MB.
MAX_MESSAGE_BYTES: Final = 4 * 1024 * 1024
# Starting Python and importing PDFium does not depend on the document, so it
# has its own generous limit and never counts against a page's time.
STARTUP_TIMEOUT_S: Final = 60.0
# A message that has started to arrive gets at least this long to finish.
MIN_READ_S: Final = 1.0
# How long to wait for a child whose pipe closed mid-message to be reaped. A
# dying process closes its pipe a moment before its parent can see it exit.
EXIT_GRACE_S: Final = 1.0
STOP_GRACE_S: Final = 0.5
_KILL_JOIN_S: Final = 5.0
# Child-supplied text in exception messages is cut to this length.
MAX_DETAIL_CHARS: Final = 300
# The only environment variables a child keeps (it needs no secrets).
ENVIRONMENT_ALLOWLIST: Final = frozenset(
    {"PATH", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE", "TESSDATA_PREFIX"}
)
_PR_SET_PDEATHSIG: Final = 1  # from <linux/prctl.h>
_LIBC: Final = ctypes.CDLL(None, use_errno=True) if sys.platform == "linux" else None

type Json = bool | int | float | str | list[Json] | dict[str, Json] | None


class ChildDiedError(Exception):
    """The child process ended without answering."""

    def __init__(self, exitcode: int | None) -> None:
        super().__init__(describe_exit(exitcode))
        self.exitcode = exitcode


class ChildTimeoutError(Exception):
    """The child did not answer in time."""


class ChildProtocolError(Exception):
    """The child sent something that is not a valid, size-capped JSON message."""


class ChildStartError(Exception):
    """A child process did not start (it died or stayed silent during start-up)."""


def clip(text: str, limit: int = MAX_DETAIL_CHARS) -> str:
    """Shorten child-supplied text before it goes into an exception message."""
    return text if len(text) <= limit else text[: limit - 3] + "..."


def describe_exit(exitcode: int | None) -> str:
    """Readable cause of a child's end, e.g. "the worker process was killed by SIGSEGV"."""
    if exitcode is None:
        return "the worker process stopped without exiting"
    if exitcode < 0:
        try:
            name = signal.Signals(-exitcode).name
        except ValueError:
            name = f"signal {-exitcode}"
        return f"the worker process was killed by {name}"
    return f"the worker process exited with code {exitcode}"


def check_finite_positive(name: str, value: float) -> None:
    """Refuse NaN, infinite, zero and negative durations."""
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a positive, finite number of seconds, not {value!r}")


def check_main_importable() -> None:
    """Fail fast if spawned children could not start.

    "spawn" children re-import the main program. When it is not a real file (a
    script piped into ``python -``), every child would crash on start; raise a
    clear error instead. Scripts that call this package must also guard their
    top-level code with ``if __name__ == "__main__":``, or each child re-runs it.
    """
    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None)
    if getattr(main, "__spec__", None) is None and path and not Path(path).is_file():
        raise RuntimeError(
            f"worker processes cannot start because the main program {path!r} is not a "
            "file (for example a script piped into Python); run a .py file or "
            "`python -m ...` instead"
        )


def die_with_parent(parent_pid: int) -> None:
    """Ask the kernel to kill this process when its parent dies (Linux only).

    If the parent already died before this took effect, exit at once.
    """
    if _LIBC is not None:
        _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
    if os.getppid() != parent_pid:
        os._exit(1)


def limit_memory(limit_bytes: int | None) -> None:
    """Cap this process's address space (Linux only; elsewhere a no-op)."""
    if limit_bytes is not None and sys.platform == "linux":
        import resource  # POSIX-only module, so imported here

        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))


def scrub_environment(environ: MutableMapping[str, str] | None = None) -> None:
    """Drop every environment variable a child does not need (secrets included).

    ``environ`` defaults to this process's environment (``os.environ``).
    """
    env = os.environ if environ is None else environ
    for name in list(env):
        if name not in ENVIRONMENT_ALLOWLIST:
            del env[name]


def send_json(conn: Connection, message: Json) -> None:
    """Child side: send one JSON message."""
    conn.send_bytes(json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def _child_main(
    conn: Connection,
    target: Callable[..., None],
    args: tuple[object, ...],
    memory_limit: int | None,
    parent_pid: int,
) -> None:
    die_with_parent(parent_pid)
    scrub_environment()
    limit_memory(memory_limit)
    try:
        send_json(conn, ["ready", os.getpid()])
        target(conn, *args)
    finally:
        conn.close()


class Child:
    """One spawned process running ``target(conn, *args)``."""

    def __init__(
        self, target: Callable[..., None], args: tuple[object, ...], memory_limit: int | None
    ) -> None:
        check_main_importable()
        context = multiprocessing.get_context("spawn")
        self.conn, child_conn = context.Pipe(duplex=True)
        self.process = context.Process(
            target=_child_main,
            args=(child_conn, target, args, memory_limit, os.getpid()),
            daemon=True,
        )
        self._timed_out = False
        try:
            self.process.start()
        except OSError as exc:
            self.conn.close()
            raise ChildStartError(f"the worker process could not be started: {exc}") from exc
        finally:
            child_conn.close()

    @property
    def sentinel(self) -> int:
        return self.process.sentinel

    def wait_ready(self, timeout: float = STARTUP_TIMEOUT_S) -> None:
        """Wait until the child has started and set its limits."""
        try:
            message = self.receive(timeout)
        except ChildTimeoutError:
            raise ChildStartError(
                f"the worker process did not start within {timeout:g} s"
            ) from None
        except (ChildDiedError, ChildProtocolError) as exc:
            raise ChildStartError(f"the worker process failed to start: {exc}") from exc
        if not (isinstance(message, list) and message and message[0] == "ready"):
            raise ChildStartError("the worker process sent an unexpected first message")

    def send(self, message: object) -> None:
        """Send a task (pickled: the parent is trusted, the child is not)."""
        try:
            self.conn.send(message)
        except (BrokenPipeError, ConnectionResetError, EOFError, OSError) as exc:
            raise ChildDiedError(self._exitcode()) from exc

    def receive(self, timeout: float) -> Json:
        """The next message, waiting at most ``timeout`` seconds for it to start arriving."""
        if not wait([self.conn, self.sentinel], max(0.0, timeout)):
            raise ChildTimeoutError
        return self.receive_ready(timeout)

    def receive_ready(self, timeout: float) -> Json:
        """Read the message of a child whose pipe or sentinel is ready.

        A watchdog kills the child if the message does not finish arriving within
        ``timeout`` (at least ``MIN_READ_S``): a child that sends half a message
        and stalls cannot block the parent.
        """
        watchdog = threading.Timer(max(timeout, MIN_READ_S), self._watchdog_kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            raw = self._read()
        finally:
            watchdog.cancel()
        if raw is None:
            if self._timed_out:
                raise ChildTimeoutError
            raise ChildDiedError(self._exitcode())
        try:
            message: Json = json.loads(raw.decode("utf-8"))
            # JSON can spell half a character ("\ud800"). No honest worker sends
            # one, and such text could not be stored or printed later: refuse it
            # here, at the boundary (UnicodeEncodeError is a ValueError).
            json.dumps(message, ensure_ascii=False).encode("utf-8")
        except (ValueError, RecursionError) as exc:
            raise ChildProtocolError("message is not UTF-8 JSON (or is nested too deeply)") from exc
        return message

    def _read(self) -> bytes | None:
        """One raw message, or None if the child closed its end (died or was killed)."""
        if not self.conn.poll():
            return None
        try:
            return self.conn.recv_bytes(MAX_MESSAGE_BYTES)
        except (EOFError, ConnectionResetError):
            return None
        except OSError as exc:  # too long, or cut off by the child's death
            if self._timed_out or self._has_exited():
                return None  # killed by the watchdog, or died, mid-message
            raise ChildProtocolError(f"message over {MAX_MESSAGE_BYTES} bytes") from exc

    def _has_exited(self) -> bool:
        """True if the child has exited, allowing ``EXIT_GRACE_S`` for it to be reaped.

        Right after its pipe closes, a dying child can still look alive: checking
        ``is_alive()`` at once misreported a death as an oversized message.
        """
        self.process.join(timeout=EXIT_GRACE_S)
        return not self.process.is_alive()

    def _watchdog_kill(self) -> None:
        self._timed_out = True
        with suppress(OSError, ValueError, AttributeError):
            self.process.kill()

    def _exitcode(self) -> int | None:
        self.process.join(timeout=1.0)
        return self.process.exitcode

    def stop(self, *, graceful: bool = True) -> None:
        """Stop the child and release its resources (see ``stop_children``)."""
        stop_children([self], graceful=graceful)


def _release(child: Child) -> None:
    """Kill the child if it still runs, then free its pipe and process handle."""
    try:
        if child.process.is_alive():
            child.process.kill()
            child.process.join(timeout=_KILL_JOIN_S)
    finally:
        child.conn.close()
        with suppress(ValueError):  # still running after SIGKILL and 5 s: leave it to the OS
            child.process.close()


def stop_children(children: Sequence[Child], *, graceful: bool) -> None:
    """Stop every child, always, even if stopping one of them fails.

    ``graceful``: idle children are asked to finish and given ``STOP_GRACE_S``
    in total (not each). Otherwise, or after that, the rest are killed at once.
    """
    if graceful:
        for child in children:
            with suppress(OSError, ValueError):
                child.conn.send(None)
        deadline = time.monotonic() + STOP_GRACE_S
        for child in children:
            with suppress(ValueError, AssertionError):
                child.process.join(timeout=max(0.0, deadline - time.monotonic()))
    first_error: BaseException | None = None
    for child in children:
        try:
            _release(child)
        except BaseException as exc:  # keep stopping the others; re-raise afterwards
            first_error = first_error or exc
    if first_error is not None:
        raise first_error


def start_child(
    target: Callable[..., None], args: tuple[object, ...], memory_limit: int | None
) -> Child:
    """Start one child and wait until it is ready."""
    return start_children(target, args, 1, memory_limit)[0]


def start_children(
    target: Callable[..., None], args: tuple[object, ...], count: int, memory_limit: int | None
) -> list[Child]:
    """Start ``count`` children in parallel and wait until all are ready.

    If any fails to start, every child already started is stopped and
    ``ChildStartError`` is raised.
    """
    children: list[Child] = []
    try:
        for _ in range(count):
            children.append(Child(target, args, memory_limit))
        for child in children:
            child.wait_ready()
    except BaseException:
        stop_children(children, graceful=False)
        raise
    return children


class TaskTimeoutError(Exception):
    """A task ran longer than its limit; its worker was killed."""

    def __init__(self, index: int) -> None:
        super().__init__(index)
        self.index = index


class TaskFailedError(Exception):
    """A task raised, its worker died, or it answered with something invalid."""

    def __init__(self, index: int, detail: str) -> None:
        super().__init__(index, detail)
        self.index = index
        self.detail = detail


class DeadlineError(Exception):
    """The overall deadline for all tasks passed before they finished."""


def task_loop(conn: Connection, function: Callable[[Any], Json]) -> None:
    """Child side of ``run_tasks``: answer tasks until told to stop."""
    while True:
        try:
            message = conn.recv()
        except EOFError:
            return
        if message is None:
            return
        index, task = message
        try:
            result = function(task)
        except Exception as exc:  # reported to the parent, which decides what to do
            send_json(conn, ["error", index, f"{type(exc).__name__}: {exc}"])
        else:
            send_json(conn, ["result", index, result])


def _parse_answer(message: Json, index: int) -> Json:
    """The result in a task answer, or ``TaskFailedError``."""
    if not (isinstance(message, list) and len(message) == 3 and message[1] == index):
        raise TaskFailedError(index, "the worker sent an unexpected message")
    kind, _, value = message
    if kind == "error":
        raise TaskFailedError(index, clip(str(value)))
    if kind != "result":
        raise TaskFailedError(index, "the worker sent an unexpected message")
    return value


def run_tasks[T, R](
    function: Callable[[T], Json],
    tasks: Sequence[T],
    *,
    decode: Callable[[Json], R],
    workers: int,
    timeout_s: float,
    memory_limit: int | None = None,
    on_result: Callable[[int, R], None] | None = None,
    deadline: float | None = None,
) -> list[R]:
    """Run ``function`` on every task in parallel child processes; results in task order.

    At most ``workers`` tasks run at once, one per child, and each task's clock
    starts when it is handed to a child. ``deadline`` (a ``time.monotonic()``
    value) bounds the whole run. The first problem stops everything at once: a
    task over ``timeout_s`` (``TaskTimeoutError``), the deadline
    (``DeadlineError``), a task that raised, a child that died, an answer
    ``decode`` rejects with ``ValueError`` (``TaskFailedError``), or
    ``on_result`` raising. ``ChildStartError`` if the children cannot start.
    Every child is killed before this returns or raises.
    """
    if not tasks:
        return []
    if workers < 1:
        raise ValueError("workers must be at least 1")
    check_finite_positive("timeout_s", timeout_s)
    if deadline is not None and not math.isfinite(deadline):
        raise ValueError(f"deadline must be finite, not {deadline!r}")
    children = start_children(task_loop, (function,), min(workers, len(tasks)), memory_limit)
    finished = False
    try:
        results = _schedule(children, tasks, decode, timeout_s, on_result, deadline)
        finished = True
        return results
    finally:
        stop_children(children, graceful=finished)


def _schedule[T, R](
    children: list[Child],
    tasks: Sequence[T],
    decode: Callable[[Json], R],
    timeout_s: float,
    on_result: Callable[[int, R], None] | None,
    deadline: float | None,
) -> list[R]:
    idle = list(children)
    busy: dict[int, tuple[Child, float]] = {}  # task index -> (child, task deadline)
    pending = deque(enumerate(tasks))
    results: dict[int, R] = {}
    while pending or busy:
        while pending and idle:
            index, task = pending.popleft()
            child = idle.pop()
            try:
                child.send((index, task))
            except ChildDiedError as exc:
                raise TaskFailedError(index, str(exc)) from exc
            busy[index] = (child, time.monotonic() + timeout_s)
        nearest = min(task_deadline for _, task_deadline in busy.values())
        if deadline is not None:
            nearest = min(nearest, deadline)
        waitables = [obj for child, _ in busy.values() for obj in (child.conn, child.sentinel)]
        ready = set(wait(waitables, timeout=max(0.0, nearest - time.monotonic())))
        for index, (child, task_deadline) in list(busy.items()):
            if child.conn in ready or child.sentinel in ready:
                del busy[index]
                remaining = task_deadline - time.monotonic()
                try:
                    value = _parse_answer(child.receive_ready(remaining), index)
                    result = decode(value)
                except ChildTimeoutError:
                    raise TaskTimeoutError(index) from None
                except (ChildDiedError, ChildProtocolError, ValueError) as exc:
                    raise TaskFailedError(index, clip(str(exc))) from exc
                results[index] = result
                if on_result is not None:
                    on_result(index, result)
                idle.append(child)
            elif time.monotonic() >= task_deadline:
                raise TaskTimeoutError(index)
        if deadline is not None and time.monotonic() >= deadline and (pending or busy):
            raise DeadlineError
    return [results[i] for i in range(len(tasks))]
