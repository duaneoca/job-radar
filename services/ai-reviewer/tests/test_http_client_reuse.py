"""The worker reuses one HTTP client per process.

`httpx.get()` builds and discards a whole Client per call, and the native memory
behind it fragments instead of returning to the OS: +31Mi per 8,000 calls in the
production image, against no growth for one reused client. Every review makes
four or five calls to tracker-api, so the worker grew ~18KB per task even on jobs
it skipped (~25KB/task in production) — invisible to tracemalloc, because it isn't
Python objects.
"""

import inspect
import re

from app import main as worker


def test_one_client_per_process(monkeypatch):
    monkeypatch.setattr(worker, "_HTTP", None)
    assert worker._http() is worker._http()


def test_a_forked_child_gets_its_own_client(monkeypatch):
    """A client inherited across fork would share the parent's sockets."""
    monkeypatch.setattr(worker, "_HTTP", None)
    parent = worker._http()
    monkeypatch.setattr(worker.os, "getpid", lambda: -1)
    assert worker._http() is not parent


def test_no_one_off_clients_in_the_worker():
    """httpx.get/post/put/delete each build a throwaway Client — the leak."""
    src = inspect.getsource(worker)
    assert not re.search(r"\bhttpx\.(get|post|put|patch|delete|request)\(", src)
