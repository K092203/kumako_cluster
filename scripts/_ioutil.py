#!/usr/bin/env python3
"""共有フォルダ(SMB)I/O をタイムアウト付き・非ブロッキングにするヘルパー。

背景: 共有フォルダへの書き込み(``os.replace`` など)は、SMB サーバが高負荷の
とき返ってこなくなることがある。Python の同期 I/O は割り込めないため、単純な
``try/except`` ではハングを救えない。2026-07 のリハーサルでは、280 スロットの
負荷下で複数の launcher / supervisor が status 書き込みで無限ハングし、以降
control ファイルの監視(停止・再起動命令の受理)まで停止した。

方針: ハングしうる I/O を **別スレッド** に隔離する。制御ループ本体は advisory な
status 書き込みを待たずに回り続け、停止・起動命令へ応答し続けられる。ハングした
スレッドは daemon として放置する(SMB が復帰すれば自然終了し、しなくても
プロセスの応答性は損なわれない)。
"""

from __future__ import annotations

import _pyversion  # noqa: F401  Pythonバージョン検査(3.9未満なら即エラー)

import threading
from typing import Any, Callable, Optional, Tuple


def call_with_timeout(
    fn: Callable[[], Any], timeout_sec: float
) -> Tuple[bool, Optional[Any], Optional[BaseException]]:
    """``fn()`` を別スレッドで実行し、時間内に終われば結果を返す。

    戻り値 ``(completed, value, error)``:
      - 時間内に正常終了: ``(True, 戻り値, None)``
      - 時間内に例外送出: ``(True, None, 例外)``
      - タイムアウト: ``(False, None, None)``(ハングしたスレッドは daemon で放置)
    """
    box: dict[str, Any] = {}
    done = threading.Event()

    def runner() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001  呼び出し側へ運ぶ
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=runner, daemon=True).start()
    if not done.wait(timeout_sec):
        return False, None, None
    return True, box.get("value"), box.get("error")


class StatusHeartbeat:
    """status ファイルへの書き込みを専用スレッドへ隔離する心拍。

    ``update(payload)`` はメモリ上の最新ペイロードを差し替えるだけで即座に返る
    (SMB I/O を一切行わない)。実際の書き込みは背景スレッドが行うため、書き込みが
    ハングしても呼び出し側の制御ループは止まらない。書き込みは最新ペイロードへ
    コアレスされる(バースト時に古い状態を無駄に書かない)。
    """

    def __init__(self, path, write_fn: Callable[[Any, Any], None], wake_sec: float = 1.0) -> None:
        self._path = path
        self._write_fn = write_fn
        self._wake_sec = wake_sec
        self._cv = threading.Condition()
        self._payload: Any = None
        self._dirty = False
        self._stopping = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "StatusHeartbeat":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def update(self, payload: Any) -> None:
        """最新の status ペイロードを差し替える。I/O は行わず即返る。"""
        with self._cv:
            self._payload = payload
            self._dirty = True
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._dirty and not self._stopping:
                    self._cv.wait(self._wake_sec)
                if self._stopping and not self._dirty:
                    return
                payload = self._payload
                self._dirty = False
            try:
                self._write_fn(self._path, payload)
            except Exception:
                # status は advisory。書き込み失敗はループを止める理由にしない。
                pass

    def close(self, final_payload: Any = None, join_sec: float = 2.0) -> None:
        """心拍を止める。final_payload があれば最後に一度だけ書き込みを試みる。

        書き込みスレッドがハング中でも ``join`` は ``join_sec`` で打ち切るため、
        呼び出し側(停止処理)が無限に待たされることはない。
        """
        if final_payload is not None:
            self.update(final_payload)
        with self._cv:
            self._stopping = True
            self._cv.notify()
        if self._thread is not None:
            self._thread.join(join_sec)
