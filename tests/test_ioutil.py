"""Tests for _ioutil: timeout-boxed I/O and the isolated status heartbeat.

2026-07 のリハーサルで launcher / supervisor が status 書き込み(SMB)で無限
ハングした事象の恒久対策。ハングする I/O があっても制御ループが止まらないことを
検証する。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _ioutil import StatusHeartbeat, call_with_timeout  # noqa: E402
from _ioutil import SingleFlightTimeout  # noqa: E402


def test_call_with_timeout_fast_returns_value():
    completed, value, error = call_with_timeout(lambda: 21 * 2, timeout_sec=1.0)
    assert completed is True
    assert value == 42
    assert error is None


def test_call_with_timeout_propagates_exception():
    def boom():
        raise ValueError("nope")

    completed, value, error = call_with_timeout(boom, timeout_sec=1.0)
    assert completed is True
    assert value is None
    assert isinstance(error, ValueError)


def test_call_with_timeout_times_out_without_blocking_caller():
    start = time.monotonic()
    completed, value, error = call_with_timeout(lambda: time.sleep(5.0), timeout_sec=0.2)
    elapsed = time.monotonic() - start
    assert completed is False
    assert value is None and error is None
    assert elapsed < 2.0  # 5秒スリープの完了を待たずに返る


def test_single_flight_timeout_returns_results_for_completed_calls():
    single_flight = SingleFlightTimeout()

    assert single_flight.call("control/stop_all", lambda: 42, timeout_sec=1.0) == (True, 42, None)
    assert single_flight.call("control/stop_all", lambda: "again", timeout_sec=1.0) == (True, "again", None)


def test_single_flight_timeout_does_not_add_thread_for_hung_key():
    single_flight = SingleFlightTimeout()
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def hang():
        entered.set()
        try:
            release.wait(10.0)
        finally:
            finished.set()

    assert single_flight.call("control/stop_all", hang, timeout_sec=0.1) == (False, None, None)
    assert entered.wait(1.0)
    thread_count = threading.active_count()

    start = time.monotonic()
    assert single_flight.call("control/stop_all", lambda: True, timeout_sec=1.0) == (False, None, None)
    assert time.monotonic() - start < 0.5
    assert threading.active_count() == thread_count

    release.set()
    assert finished.wait(1.0)


def test_single_flight_timeout_allows_different_key_while_one_is_hung():
    single_flight = SingleFlightTimeout()
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def hang():
        entered.set()
        try:
            release.wait(10.0)
        finally:
            finished.set()

    assert single_flight.call("control/stop_all", hang, timeout_sec=0.1) == (False, None, None)
    assert entered.wait(1.0)
    assert single_flight.call("control/worker.stop", lambda: "available", timeout_sec=1.0) == (
        True,
        "available",
        None,
    )

    release.set()
    assert finished.wait(1.0)


def test_heartbeat_writes_latest_payload():
    written = []
    flushed = threading.Event()

    def write_fn(path, payload):
        written.append((path, payload))
        flushed.set()

    hb = StatusHeartbeat("statuspath", write_fn).start()
    hb.update({"status": "running"})
    assert flushed.wait(2.0)
    hb.close()
    assert written[-1][0] == "statuspath"
    assert written[-1][1]["status"] == "running"


def test_heartbeat_update_and_close_survive_a_hung_write():
    entered = threading.Event()
    release = threading.Event()

    def hanging_write(path, payload):
        entered.set()
        release.wait(10.0)  # 書き込みが返ってこない状況を模す

    hb = StatusHeartbeat("statuspath", hanging_write).start()
    hb.update({"n": 1})
    assert entered.wait(2.0)  # 書き込みスレッドが起動しハング中

    # 書き込みがハングしていても update() は即座に返る(制御ループを止めない)
    start = time.monotonic()
    hb.update({"n": 2})
    assert time.monotonic() - start < 0.5

    # close() もハングした書き込みを join_sec で打ち切り、無限に待たない
    start = time.monotonic()
    hb.close(join_sec=0.3)
    assert time.monotonic() - start < 2.0

    release.set()  # daemon スレッドを後始末
