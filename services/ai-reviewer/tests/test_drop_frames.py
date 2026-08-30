"""_drop_frames must strip retained locals without changing exception behaviour.

The measured facts it exists for are in the function's own docstring; what is
tested here is the contract: frames in the cause/context chain are emptied, the
exception itself stays raisable and chain-intact, and pathological chains
(cycles, still-executing frames) cannot make it blow up inside an except block
— where an exception from the fix would replace the one being handled.
"""

import pytest

from app.main import _drop_frames


def _make_chained():
    """An exception chain shaped like production's: cause carries a fat local."""
    def inner():
        fat_inner = {"payload": "y" * 50_000}          # noqa: F841 — the point
        raise ConnectionError("provider 429")

    def outer():
        fat_outer = "x" * 50_000                        # noqa: F841 — the point
        try:
            inner()
        except ConnectionError as cause:
            raise RuntimeError("LLM call failed") from cause

    try:
        outer()
    except RuntimeError as exc:
        return exc


def _chain_frames(exc):
    frames = []
    while exc is not None:
        tb = exc.__traceback__
        while tb is not None:
            frames.append(tb.tb_frame)
            tb = tb.tb_next
        exc = exc.__cause__ or exc.__context__
    return frames


def test_clears_locals_through_the_cause_chain():
    exc = _make_chained()
    assert any(f.f_locals for f in _chain_frames(exc)), "test built no locals"
    _drop_frames(exc)
    for f in _chain_frames(exc):
        assert f.f_locals == {}, f"{f.f_code.co_name} still holds locals"


def test_exception_survives_and_chain_is_intact():
    exc = _make_chained()
    _drop_frames(exc)
    assert isinstance(exc.__cause__, ConnectionError)
    with pytest.raises(RuntimeError, match="LLM call failed"):
        raise exc


def test_context_cycle_terminates():
    a, b = ValueError("a"), ValueError("b")
    a.__context__, b.__context__ = b, a
    _drop_frames(a)  # must not loop forever


def test_none_is_a_noop():
    _drop_frames(None)


def test_still_executing_frame_is_skipped_not_fatal():
    """The raising frame is still on the stack when the task calls this from
    its except block; frame.clear() raises RuntimeError there. It must be
    swallowed — the caller deletes its own locals instead."""
    try:
        raise ValueError("live frame in traceback")
    except ValueError as exc:
        _drop_frames(exc)   # our own frame is in exc.__traceback__, executing
