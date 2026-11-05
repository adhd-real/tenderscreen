"""Run work on untrusted documents in child processes the parent fully controls.

Every child is a fresh "spawn" process with a memory cap (Linux ``RLIMIT_AS``,
which programs it starts, such as Tesseract, inherit). The parent waits on the
child's pipe and its process sentinel together, so it learns at once when a
child dies (a PDFium crash, the memory cap, the OOM killer) and can kill exactly
the child that overran its time limit. ``multiprocessing.Pool`` is not used: it
cannot tell a dead worker from a slow one, and its shutdown can hang forever
after a worker is killed.

Children answer in size-capped JSON, never pickle: a child parses untrusted
documents, so whatever it sends back is treated as untrusted too (CLAUDE.md
security rules: never unpickle anything derived from a document).
"""

import json
import multiprocessing
import os
import signal
import sys
import time
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import suppress
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any, Final

# Largest message a child may send (one page's text and metadata).
MAX_MESSAGE_BYTES: Final = 16 * 1024 * 1024
# Starting Python and importing PDFium does not depend on the document, so it
# has its own generous limit and never counts against a page's time.
STARTUP_TIMEOUT_S: Final = 60.0
_KILL_JOIN_S: Final = 5.0
_STOP_GRACE_S: Final = 0.5

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


def limit_memory(limit_bytes: int | None) -> None:
    """Cap this process's address space (Linux only; elsewhere a no-op)."""
    if limit_bytes is not None and sys.platform == "linux":
        import resource  # POSIX-only module, so imported here

        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))


def send_json(conn: Connection, message: Json) -> None:
    """Child side: send one JSON message."""
    conn.send_bytes(json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def _child_main(
    conn: Connection,
    target: Callable[..., None],
    args: tuple[object, ...],
    memory_limit: int | None,
) -> None:
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
            target=_child_main, args=(child_conn, target, args, memory_limit), daemon=True
        )
        self.process.start()
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
        """The next message, waiting at most ``timeout`` seconds."""
        if not wait([self.conn, self.sentinel], timeout):
            raise ChildTimeoutError
        return self.receive_ready()

    def receive_ready(self) -> Json:
        """Read the message of a child whose pipe or sentinel is ready."""
        if self.conn.poll():
            try:
                raw = self.conn.recv_bytes(MAX_MESSAGE_BYTES)
            except (EOFError, ConnectionResetError):
                pass  # the child closed its end: it died
            except OSError as exc:  # longer than MAX_MESSAGE_BYTES
                raise ChildProtocolError(f"message over {MAX_MESSAGE_BYTES} bytes") from exc
            else:
                try:
                    message: Json = json.loads(raw.decode("utf-8"))
                except ValueError as exc:
                    raise ChildProtocolError("message is not UTF-8 JSON") from exc
                return message
        raise ChildDiedError(self._exitcode())

    def _exitcode(self) -> int | None:
        self.process.join(timeout=1.0)
        return self.process.exitcode

    def stop(self) -> None:
        """Ask the child to finish, kill it if it does not, and release its resources."""
        if self.process.is_alive():
            with suppress(OSError, ValueError):
                self.conn.send(None)
            self.process.join(timeout=_STOP_GRACE_S)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=_KILL_JOIN_S)
        self.conn.close()
        with suppress(ValueError):  # still running after SIGKILL and 5 s: leave it to the OS
            self.process.close()


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
        for child in children:
            child.stop()
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
        raise TaskFailedError(index, str(value))
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
) -> list[R]:
    """Run ``function`` on every task in parallel child processes; results in task order.

    At most ``workers`` tasks run at once, one per child, and each task's clock
    starts when it is handed to a child. The first problem stops everything at
    once: a task over ``timeout_s`` (``TaskTimeoutError``), a task that raised, a
    child that died, an answer ``decode`` rejects with ``ValueError``
    (``TaskFailedError``), or ``on_result`` raising. ``ChildStartError`` if the
    children cannot start. Every child is killed before this returns or raises.
    """
    if not tasks:
        return []
    if workers < 1 or timeout_s <= 0:
        raise ValueError("workers must be at least 1 and timeout_s positive")
    children = start_children(task_loop, (function,), min(workers, len(tasks)), memory_limit)
    try:
        return _schedule(children, tasks, decode, timeout_s, on_result)
    finally:
        for child in children:
            child.stop()


def _schedule[T, R](
    children: list[Child],
    tasks: Sequence[T],
    decode: Callable[[Json], R],
    timeout_s: float,
    on_result: Callable[[int, R], None] | None,
) -> list[R]:
    idle = list(children)
    busy: dict[int, tuple[Child, float]] = {}  # task index -> (child, deadline)
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
        nearest = min(deadline for _, deadline in busy.values())
        waitables = [obj for child, _ in busy.values() for obj in (child.conn, child.sentinel)]
        ready = set(wait(waitables, timeout=max(0.0, nearest - time.monotonic())))
        for index, (child, deadline) in list(busy.items()):
            if child.conn in ready or child.sentinel in ready:
                del busy[index]
                try:
                    value = _parse_answer(child.receive_ready(), index)
                    result = decode(value)
                except (ChildDiedError, ChildProtocolError, ValueError) as exc:
                    raise TaskFailedError(index, str(exc)) from exc
                results[index] = result
                if on_result is not None:
                    on_result(index, result)
                idle.append(child)
            elif time.monotonic() >= deadline:
                raise TaskTimeoutError(index)
    return [results[i] for i in range(len(tasks))]
