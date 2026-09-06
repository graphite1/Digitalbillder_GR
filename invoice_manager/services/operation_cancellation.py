"""Cooperative cancellation; browser and database objects stay on their worker."""
from __future__ import annotations

import threading
from contextlib import contextmanager


class OperationCancelled(Exception):
    def __init__(self):
        super().__init__("処理を中断しました。保存前の取得結果は反映していません。")


class CancellationToken:
    def __init__(self):
        self._lock = threading.Lock()
        self._requested = False
        self._accepting = True

    @property
    def requested(self):
        with self._lock:
            return self._requested

    @property
    def can_cancel(self):
        with self._lock:
            return self._accepting and not self._requested

    def request(self):
        with self._lock:
            if not self._accepting or self._requested:
                return False
            self._requested = True
            return True

    def check(self):
        with self._lock:
            if self._requested:
                raise OperationCancelled()

    def begin_commit(self):
        """Atomically reject cancellation after the last safe save boundary."""
        with self._lock:
            if self._requested:
                raise OperationCancelled()
            self._accepting = False


_local = threading.local()


def current_token():
    return getattr(_local, "token", None)


@contextmanager
def cancellation_scope(token):
    previous = current_token()
    _local.token = token
    try:
        check_cancelled()
        yield
    finally:
        _local.token = previous


def check_cancelled():
    token = current_token()
    if token is not None:
        token.check()


def begin_commit():
    token = current_token()
    if token is not None:
        token.begin_commit()
