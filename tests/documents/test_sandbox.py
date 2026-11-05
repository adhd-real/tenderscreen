import json
import multiprocessing
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from multiprocessing.connection import Connection
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from tenderscreen.documents import sandbox
from tenderscreen.documents.sandbox import (
    MAX_MESSAGE_BYTES,
    Child,
    ChildDiedError,
    ChildProtocolError,
    ChildStartError,
    ChildTimeoutError,
    DeadlineError,
    Json,
    TaskFailedError,
    TaskTimeoutError,
    check_main_importable,
    describe_exit,
    run_tasks,
    send_json,
    start_child,
    task_loop,
)
from tests.documents.worker_tasks import (
    allocate,
    die_mid_message,
    die_soon,
    fail,
    oversized,
    report_environment,
    segfault,
    silent,
    sleep_and_record,
    square,
    stall_mid_message,
    unserializable,
)


def identity(value: Json) -> Json:
    return value


def run(
    function: Callable[[Any], Json],
    tasks: Sequence[Any],
    *,
    workers: int = 2,
    timeout_s: float = 60.0,
    **options: Any,
) -> list[Any]:
    decode = options.pop("decode", identity)
    return run_tasks(
        function, tasks, decode=decode, workers=workers, timeout_s=timeout_s, **options
    )


@pytest.fixture
def quick_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """For tests whose child never reads the stop message: it is killed after the
    stop grace either way, and the default 0.5 s would only make the test slow."""
    monkeypatch.setattr(sandbox, "STOP_GRACE_S", 0.05)


@pytest.fixture
def quick_exit_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """For tests where a child stays alive after a cut-off message: the default
    1 s wait to see whether it exits would only make the test slow."""
    monkeypatch.setattr(sandbox, "EXIT_GRACE_S", 0.1)


def test_results_come_back_in_task_order() -> None:
    assert run(square, [3, 1, 2, 5]) == [9, 1, 4, 25]


def test_no_tasks_starts_no_process() -> None:
    assert run(square, []) == []


def test_invalid_worker_count() -> None:
    with pytest.raises(ValueError, match="workers must be at least 1"):
        run(square, [1], workers=0)


def test_tasks_run_in_parallel_worker_processes() -> None:
    """Never one page at a time: with 3 workers, 3 sleeping tasks overlap in time.

    0.3 s each: the three are handed out within milliseconds of one another, so
    they overlap unless they run in sequence, even on a slow runner."""
    records = [cast(list[float], r) for r in run(sleep_and_record, [0.3, 0.3, 0.3], workers=3)]
    pids = {int(pid) for pid, _, _ in records}
    assert os.getpid() not in pids
    assert len(pids) == 3
    latest_start = max(start for _, start, _ in records)
    earliest_end = min(end for _, _, end in records)
    assert latest_start < earliest_end, "tasks did not overlap, so they ran in sequence"


def test_task_exception_is_reported_with_its_index() -> None:
    with pytest.raises(TaskFailedError) as caught:
        run(fail, [7], workers=1)
    assert (caught.value.index, caught.value.detail) == (0, "ValueError: cannot handle 7")


def test_overrunning_task_is_killed_and_reported() -> None:
    started = time.monotonic()
    with pytest.raises(TaskTimeoutError) as caught:
        # 0.5 s: ample for the instant task 0 to answer, far below task 1's 30 s.
        run(sleep_and_record, [0.0, 30.0], timeout_s=0.5)
    assert caught.value.index == 1
    assert time.monotonic() - started < 15


def test_crash_is_reported_at_once_not_as_a_timeout() -> None:
    started = time.monotonic()
    with pytest.raises(TaskFailedError, match="killed by SIGSEGV"):
        run(segfault, [1], workers=1)
    assert time.monotonic() - started < 15  # far below the 60 s limit


def test_worker_dying_between_tasks_is_reported_without_hanging() -> None:
    started = time.monotonic()
    with pytest.raises(TaskFailedError, match="exited with code 3"):
        run(die_soon, [1, 2, 3, 4], workers=1)
    assert time.monotonic() - started < 15


def test_first_failure_stops_everything_at_once() -> None:
    def reject(index: int, value: Json) -> None:
        raise RuntimeError(f"stop after task {index}")

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="stop after task 0"):
        run(sleep_and_record, [0.1, 30.0, 30.0, 30.0], on_result=reject)
    assert time.monotonic() - started < 15


def test_decode_rejection_is_a_failure() -> None:
    def strict(value: Json) -> int:
        raise ValueError(f"bad answer {value!r}")

    with pytest.raises(TaskFailedError, match="bad answer 4"):
        run(square, [2], decode=strict, workers=1)


@pytest.mark.skipif(sys.platform != "linux", reason="memory caps use Linux RLIMIT_AS")
def test_memory_cap_stops_a_greedy_task() -> None:
    cap = 256 * 1024 * 1024
    with pytest.raises(TaskFailedError, match="MemoryError"):
        run(allocate, [1024], workers=1, memory_limit=cap)
    assert run(allocate, [16], workers=1, memory_limit=cap) == [16 * 1024 * 1024]


def test_answer_that_is_not_json_is_a_failure() -> None:
    with pytest.raises(TaskFailedError, match="worker process exited with code 1"):
        run(unserializable, [1], workers=1)


def test_oversized_answer_is_refused(quick_exit_check: None) -> None:
    with pytest.raises(TaskFailedError, match=f"message over {MAX_MESSAGE_BYTES} bytes"):
        run(oversized, [1], workers=1)


def test_describe_exit() -> None:
    assert describe_exit(None) == "the worker process stopped without exiting"
    assert describe_exit(-9) == "the worker process was killed by SIGKILL"
    assert describe_exit(-200) == "the worker process was killed by signal 200"
    assert describe_exit(2) == "the worker process exited with code 2"


def test_silent_child_times_out_and_stop_kills_it(quick_stop: None) -> None:
    child = start_child(silent, (), None)
    pid = child.process.pid
    with pytest.raises(ChildTimeoutError):
        child.receive(0.1)
    child.stop()  # it never reads the stop message, so it is killed
    assert pid is not None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_child_killed_before_ready_is_a_start_error() -> None:
    child = Child(silent, (), None)
    child.process.kill()
    with pytest.raises(ChildStartError, match="failed to start"):
        child.wait_ready()
    child.stop()


def never_answers(self: Child, timeout: float) -> Json:
    raise ChildTimeoutError


def test_child_silent_on_start_is_a_start_error(
    monkeypatch: pytest.MonkeyPatch, quick_stop: None
) -> None:
    monkeypatch.setattr(Child, "receive", never_answers)
    child = Child(silent, (), None)
    with pytest.raises(ChildStartError, match="did not start within 1 s"):
        child.wait_ready(timeout=1.0)
    child.stop()


@pytest.mark.parametrize("first", [["hello"], "ready", []])
def test_unexpected_first_message_is_a_start_error(
    monkeypatch: pytest.MonkeyPatch, first: Json, quick_stop: None
) -> None:
    monkeypatch.setattr(Child, "receive", lambda self, timeout: first)
    child = Child(silent, (), None)
    with pytest.raises(ChildStartError, match="unexpected first message"):
        child.wait_ready()
    child.stop()


def send_until_broken(child: Child) -> None:
    for _ in range(100):  # the pipe buffer may take a few writes to notice
        child.send(b"x" * 65536)


def test_sending_to_a_dead_child_reports_its_death() -> None:
    child = start_child(silent, (), None)
    child.process.kill()
    child.process.join()
    with pytest.raises(ChildDiedError, match="SIGKILL"):
        send_until_broken(child)
    child.stop()


# Child-side code, run in this process so coverage sees it.


def pipe() -> tuple[Connection, Connection]:
    return multiprocessing.Pipe(duplex=True)


def test_task_loop_answers_until_told_to_stop() -> None:
    parent, child = pipe()
    parent.send((0, 4))
    parent.send((1, 0))
    parent.send(None)
    task_loop(child, square)
    assert [json.loads(parent.recv_bytes()) for _ in range(2)] == [
        ["result", 0, 16],
        ["result", 1, 0],
    ]


def test_task_loop_reports_exceptions() -> None:
    parent, child = pipe()
    parent.send((2, 9))
    parent.send(None)
    task_loop(child, fail)
    assert json.loads(parent.recv_bytes()) == ["error", 2, "ValueError: cannot handle 9"]


def test_task_loop_stops_at_end_of_input() -> None:
    parent, child = pipe()
    parent.close()
    task_loop(child, square)  # returns instead of waiting forever


def test_child_main_sets_limits_then_sends_ready_then_runs_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    # In this process these would cap pytest itself, so record instead of running them.
    monkeypatch.setattr(sandbox, "die_with_parent", lambda pid: calls.append(("parent", pid)))
    monkeypatch.setattr(sandbox, "scrub_environment", lambda: calls.append("scrub"))
    monkeypatch.setattr(sandbox, "limit_memory", lambda limit: calls.append(("memory", limit)))
    parent, child = pipe()
    sandbox._child_main(child, send_hello, ("hello",), 123, 456)
    assert calls == [("parent", 456), "scrub", ("memory", 123)]
    assert json.loads(parent.recv_bytes()) == ["ready", os.getpid()]
    assert json.loads(parent.recv_bytes()) == ["said", "hello"]


def send_hello(conn: Connection, word: str) -> None:
    send_json(conn, ["said", word])


def test_receive_ready_rejects_non_json(quick_stop: None) -> None:
    child = start_child(silent, (), None)
    with pytest.MonkeyPatch.context() as patch:  # undone before stop(); quick_stop stays
        patch.setattr(child.conn, "poll", lambda: True)
        patch.setattr(child.conn, "recv_bytes", lambda limit: b"\xff not json")
        with pytest.raises(ChildProtocolError, match="not UTF-8 JSON"):
            child.receive_ready(1.0)
    child.stop()


def test_limit_memory_sets_address_space_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import resource

    calls: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(resource, "setrlimit", lambda kind, limits: calls.append((kind, limits)))
    sandbox.limit_memory(None)
    assert calls == []
    sandbox.limit_memory(123)
    if sys.platform == "linux":
        assert calls == [(resource.RLIMIT_AS, (123, 123))]


def test_parse_answer_rejects_malformed_messages() -> None:
    malformed: list[Json] = [None, ["result", 1], ["result", 9, 1], ["weird", 0, 1]]
    for message in malformed:
        with pytest.raises(TaskFailedError, match="unexpected message"):
            sandbox._parse_answer(message, 0)
    with pytest.raises(TaskFailedError, match="boom"):
        sandbox._parse_answer(["error", 0, "boom"], 0)
    assert sandbox._parse_answer(["result", 0, [1, 2]], 0) == [1, 2]


def fake_main(file: str | None, *, spec: bool) -> ModuleType:
    module = ModuleType("__main__")
    if file is not None:
        module.__file__ = file
    module.__spec__ = sys.modules[__name__].__spec__ if spec else None
    return module


def test_piped_main_script_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "__main__", fake_main("<stdin>", spec=False))
    with pytest.raises(RuntimeError, match="'<stdin>' is not a file"):
        run(square, [1], workers=1)


@pytest.mark.parametrize(
    ("file", "spec"),
    [
        (__file__, False),  # python path/to/script.py
        ("<stdin>", True),  # python -m package: re-imported by name
        (None, False),  # interactive interpreter: nothing to re-import
    ],
)
def test_importable_mains_pass(
    monkeypatch: pytest.MonkeyPatch, file: str | None, spec: bool
) -> None:
    monkeypatch.setitem(sys.modules, "__main__", fake_main(file, spec=spec))
    check_main_importable()


def test_real_test_runner_main_is_importable() -> None:
    check_main_importable()
    assert Path(__file__).is_file()


def test_start_children_stops_every_child_when_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[Child] = []
    original_init = Child.__init__

    def recording_init(self: Child, *args: Any) -> None:
        original_init(self, *args)
        started.append(self)

    def second_fails(self: Child, timeout: float = 60.0) -> None:
        if self is started[1]:
            raise ChildStartError("second child failed")

    monkeypatch.setattr(Child, "__init__", recording_init)
    monkeypatch.setattr(Child, "wait_ready", second_fails)
    with pytest.raises(ChildStartError, match="second child failed"):
        sandbox.start_children(silent, (), 3, None)
    assert len(started) == 3
    for child in started:
        with pytest.raises(ValueError, match="closed"):
            child.process.is_alive()  # stopped and closed


class DeadChild:
    """A child that died while idle: sending it a task fails."""

    def send(self, message: object) -> None:
        raise ChildDiedError(-9)


def test_sending_a_task_to_a_dead_idle_child_fails_that_task() -> None:
    with pytest.raises(TaskFailedError, match="killed by SIGKILL") as caught:
        sandbox._schedule([cast(Child, DeadChild())], [7], identity, 60.0, None, None)
    assert caught.value.index == 0


# Found in the second review of the sandbox.


def wait_for_half_message(child: Child, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wait (generously) until the stalled message has started to arrive, then make
    the watchdog quick, so a short timeout cannot end the test before the watchdog."""
    assert child.conn.poll(30), "the child never started its message"
    monkeypatch.setattr(sandbox, "MIN_READ_S", 0.05)


def test_half_sent_message_cannot_hang_the_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child that sends a message header and then stalls is cut off by the watchdog."""
    child = start_child(stall_mid_message, (), None)
    wait_for_half_message(child, monkeypatch)
    started = time.monotonic()
    with pytest.raises(ChildTimeoutError):
        child.receive_ready(0.1)
    assert child._timed_out  # cut off by the watchdog, not by waiting for data
    assert time.monotonic() - started < 10
    child.stop()


def test_watchdog_kill_is_a_timeout_even_while_the_child_still_looks_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found by CI: the killed child's pipe closed before it could be reaped, so
    ``is_alive()`` was still True and the timeout was misreported as an oversized
    message. Here ``is_alive()`` always says True, as in that moment."""
    child = start_child(stall_mid_message, (), None)
    wait_for_half_message(child, monkeypatch)
    monkeypatch.setattr(child.process, "is_alive", lambda: True)
    with pytest.raises(ChildTimeoutError):
        child.receive_ready(0.1)
    assert child._timed_out
    monkeypatch.undo()
    child.stop()


@pytest.mark.parametrize("attempt", range(3))
def test_child_that_dies_mid_message_is_a_death_not_a_size_error(attempt: int) -> None:
    child = start_child(die_mid_message, (), None)
    with pytest.raises(ChildDiedError, match="exited with code 5"):
        child.receive(10.0)
    child.stop()


def test_has_exited_waits_briefly_for_a_dying_child(quick_exit_check: None) -> None:
    child = start_child(silent, (), None)
    started = time.monotonic()
    assert not child._has_exited()  # alive: waits the full grace, then says so
    assert time.monotonic() - started >= sandbox.EXIT_GRACE_S * 0.9
    child.process.kill()
    assert child._has_exited()
    child.stop()


def test_overall_deadline_stops_the_run() -> None:
    started = time.monotonic()
    with pytest.raises(DeadlineError):
        # Even if the deadline passes while the workers start, the run stops.
        run(sleep_and_record, [30.0, 30.0], deadline=time.monotonic() + 0.3)
    assert time.monotonic() - started < 10


def test_finished_tasks_are_not_cut_by_the_deadline() -> None:
    assert run(square, [2, 3], deadline=time.monotonic() + 60) == [4, 9]


def test_failure_kills_busy_workers_at_once() -> None:
    """Found in review: each busy worker used to get its own 0.5 s grace, in turn."""

    def reject(index: int, value: Json) -> None:
        raise RuntimeError("stop")

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="stop"):
        run(sleep_and_record, [0.0, 30.0, 30.0, 30.0], workers=4, on_result=reject)
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.0])
def test_timeouts_must_be_finite_and_positive(bad: float) -> None:
    with pytest.raises(ValueError, match="positive, finite"):
        run(square, [1], timeout_s=bad)


def test_deadline_must_be_finite() -> None:
    with pytest.raises(ValueError, match="deadline must be finite"):
        run(square, [1], deadline=float("nan"))


def test_deeply_nested_answer_is_a_protocol_error(quick_stop: None) -> None:
    child = start_child(silent, (), None)
    with pytest.MonkeyPatch.context() as patch:  # undone before stop(); quick_stop stays
        patch.setattr(child.conn, "poll", lambda: True)
        patch.setattr(child.conn, "recv_bytes", lambda limit: b"[" * 200_000)
        with pytest.raises(ChildProtocolError, match="nested too deeply"):
            child.receive_ready(1.0)
    child.stop()


def test_cut_off_message_from_a_dead_child_is_a_death_not_a_size_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = start_child(silent, (), None)
    child.process.kill()
    child.process.join()

    def cut_off(limit: int) -> bytes:
        raise OSError("bad message length")

    monkeypatch.setattr(child.conn, "poll", lambda: True)
    monkeypatch.setattr(child.conn, "recv_bytes", cut_off)
    with pytest.raises(ChildDiedError, match="SIGKILL"):
        child.receive_ready(1.0)
    monkeypatch.undo()
    child.stop()


def test_process_that_cannot_start_is_a_start_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_more_processes(self: object) -> None:
        raise OSError("Resource temporarily unavailable")

    monkeypatch.setattr("multiprocessing.context.SpawnProcess.start", no_more_processes)
    with pytest.raises(ChildStartError, match="could not be started"):
        start_child(silent, (), None)


def test_children_see_only_allowlisted_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENDERSCREEN_FAKE_SECRET", "not-for-workers")
    child = start_child(report_environment, (), None)
    names = child.receive(30.0)
    child.stop()
    assert isinstance(names, list)
    assert "TENDERSCREEN_FAKE_SECRET" not in names
    assert set(cast(list[str], names)) <= sandbox.ENVIRONMENT_ALLOWLIST


def test_scrub_environment_keeps_only_the_allowlist() -> None:
    environ = {"PATH": "/bin", "LANG": "C.UTF-8", "WHATSAPP_TOKEN": "x", "HOME": "/root"}
    sandbox.scrub_environment(environ)
    assert environ == {"PATH": "/bin", "LANG": "C.UTF-8"}


class RecordingLibc:
    """Stands in for libc: records prctl calls instead of making them."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def prctl(self, option: int, value: int) -> int:
        self.calls.append((option, value))
        return 0


def test_die_with_parent_sets_parent_death_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    libc = RecordingLibc()
    monkeypatch.setattr(sandbox, "_LIBC", libc)
    sandbox.die_with_parent(os.getppid())
    assert libc.calls == [(1, signal.SIGKILL)]  # PR_SET_PDEATHSIG


def test_die_with_parent_exits_if_the_parent_is_already_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exits: list[int] = []
    monkeypatch.setattr(sandbox, "_LIBC", None)  # the no-op used outside Linux
    monkeypatch.setattr("tenderscreen.documents.sandbox.os._exit", exits.append)
    sandbox.die_with_parent(os.getppid() + 1_000_000)
    assert exits == [1]


@pytest.mark.skipif(sys.platform != "linux", reason="parent-death signal is Linux-only")
def test_workers_die_with_their_parent(tmp_path: Path) -> None:
    """Found in review: killing the parent with SIGKILL used to orphan its workers."""
    script = tmp_path / "parent.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path[:0] = {sys.path!r}\n"
        "from tenderscreen.documents.sandbox import start_child\n"
        "from tests.documents.worker_tasks import silent\n"
        "if __name__ == '__main__':\n"
        "    child = start_child(silent, (), None)\n"
        "    print(child.process.pid, flush=True)\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )
    parent = subprocess.Popen(  # noqa: S603 - our own script
        [sys.executable, str(script)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline())
        parent.kill()
        parent.wait(timeout=10)
        deadline = time.monotonic() + 10
        while process_alive(child_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not process_alive(child_pid), "the worker outlived its killed parent"
    finally:
        parent.kill()


def process_alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def test_clip_shortens_long_details() -> None:
    assert sandbox.clip("short") == "short"
    clipped = sandbox.clip("x" * 1000)
    assert len(clipped) == sandbox.MAX_DETAIL_CHARS
    assert clipped.endswith("...")


def test_ready_sentinel_without_data_is_a_death(monkeypatch: pytest.MonkeyPatch) -> None:
    child = start_child(silent, (), None)
    child.process.kill()
    child.process.join()
    monkeypatch.setattr(child.conn, "poll", lambda: False)
    with pytest.raises(ChildDiedError, match="SIGKILL"):
        child.receive_ready(1.0)
    monkeypatch.undo()
    child.stop()


class BrokenProcess:
    """A process handle whose checks fail, as during interpreter shutdown."""

    def is_alive(self) -> bool:
        raise RuntimeError("cannot check")

    def close(self) -> None:
        pass


class RecordingConn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeChild:
    def __init__(self, process: object) -> None:
        self.process = process
        self.conn = RecordingConn()


class FinishedProcess:
    def is_alive(self) -> bool:
        return False

    def close(self) -> None:
        pass


def test_stopping_continues_after_one_child_fails_and_reraises() -> None:
    broken = FakeChild(BrokenProcess())
    healthy = FakeChild(FinishedProcess())
    with pytest.raises(RuntimeError, match="cannot check"):
        sandbox.stop_children([cast(Child, broken), cast(Child, healthy)], graceful=False)
    assert broken.conn.closed
    assert healthy.conn.closed


class StallingChild:
    """Its pipe looks readable, but reading the answer times out (watchdog fired)."""

    def __init__(self) -> None:
        self.conn, self._writer = multiprocessing.Pipe(duplex=True)
        self._writer.send_bytes(b"[")  # makes conn readable
        self._idle_read, self._idle_write = os.pipe()
        self.sentinel = self._idle_read

    def send(self, message: object) -> None:
        pass

    def receive_ready(self, timeout: float) -> Json:
        raise ChildTimeoutError

    def close(self) -> None:
        self.conn.close()
        self._writer.close()
        os.close(self._idle_read)
        os.close(self._idle_write)


def test_answer_cut_off_by_the_watchdog_is_a_task_timeout() -> None:
    child = StallingChild()
    try:
        with pytest.raises(TaskTimeoutError) as caught:
            sandbox._schedule([cast(Child, child)], [1], identity, 60.0, None, None)
        assert caught.value.index == 0
    finally:
        child.close()
