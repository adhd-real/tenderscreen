import json
import multiprocessing
import os
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
    die_soon,
    fail,
    oversized,
    segfault,
    silent,
    sleep_and_record,
    square,
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


def test_results_come_back_in_task_order() -> None:
    assert run(square, [3, 1, 2, 5]) == [9, 1, 4, 25]


def test_no_tasks_starts_no_process() -> None:
    assert run(square, []) == []


@pytest.mark.parametrize(("workers", "timeout_s"), [(0, 1.0), (1, 0.0)])
def test_invalid_arguments(workers: int, timeout_s: float) -> None:
    with pytest.raises(ValueError, match="workers must be at least 1"):
        run(square, [1], workers=workers, timeout_s=timeout_s)


def test_tasks_run_in_parallel_worker_processes() -> None:
    """Never one page at a time: with 3 workers, 3 sleeping tasks overlap in time."""
    records = [cast(list[float], r) for r in run(sleep_and_record, [1.0, 1.0, 1.0], workers=3)]
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
        run(sleep_and_record, [0.0, 30.0], timeout_s=2.0)
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


def test_oversized_answer_is_refused() -> None:
    with pytest.raises(TaskFailedError, match=f"message over {MAX_MESSAGE_BYTES} bytes"):
        run(oversized, [1], workers=1)


def test_describe_exit() -> None:
    assert describe_exit(None) == "the worker process stopped without exiting"
    assert describe_exit(-9) == "the worker process was killed by SIGKILL"
    assert describe_exit(-200) == "the worker process was killed by signal 200"
    assert describe_exit(2) == "the worker process exited with code 2"


def test_silent_child_times_out_and_stop_kills_it() -> None:
    child = start_child(silent, (), None)
    pid = child.process.pid
    with pytest.raises(ChildTimeoutError):
        child.receive(0.2)
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


def test_child_silent_on_start_is_a_start_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Child, "receive", never_answers)
    child = Child(silent, (), None)
    with pytest.raises(ChildStartError, match="did not start within 1 s"):
        child.wait_ready(timeout=1.0)
    child.stop()


@pytest.mark.parametrize("first", [["hello"], "ready", []])
def test_unexpected_first_message_is_a_start_error(
    monkeypatch: pytest.MonkeyPatch, first: Json
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


def test_child_main_sends_ready_then_runs_target() -> None:
    parent, child = pipe()
    sandbox._child_main(child, send_hello, ("hello",), None)
    assert json.loads(parent.recv_bytes()) == ["ready", os.getpid()]
    assert json.loads(parent.recv_bytes()) == ["said", "hello"]


def send_hello(conn: Connection, word: str) -> None:
    send_json(conn, ["said", word])


def test_receive_ready_rejects_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    child = start_child(silent, (), None)
    monkeypatch.setattr(child.conn, "poll", lambda: True)
    monkeypatch.setattr(child.conn, "recv_bytes", lambda limit: b"\xff not json")
    with pytest.raises(ChildProtocolError, match="not UTF-8 JSON"):
        child.receive_ready()
    monkeypatch.undo()
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
        sandbox._schedule([cast(Child, DeadChild())], [7], identity, 60.0, None)
    assert caught.value.index == 0
