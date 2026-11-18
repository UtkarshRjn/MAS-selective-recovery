"""Time, tokens, cancellation, and monitor-overhead measurements.

Two clocks are kept for every event. Loop time is the asyncio clock; under
`VirtualClock` it is a deterministic simulated clock, used by the mechanism
suite so modeled agent durations produce exact interleavings. Wall time is
`perf_counter` and measures the monitor's own CPU and lock overhead. Reports
must say which one a latency number came from.

Cancelling a client coroutine does not prove that server inference stopped or
that billing ceased. A request log therefore records the cancel request, the
acknowledgment, and when the server would have finished, and whether the
tokens after the cancel are observable at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import selectors
import time
from dataclasses import dataclass, field
from typing import Any, Iterator


class TimedLock(asyncio.Lock):
    """An asyncio lock that records acquisitions, wait time, and hold time (wall clock)."""

    def __init__(self) -> None:
        super().__init__()
        self.acquisitions = 0
        self.wait_s = 0.0
        self.held_s = 0.0
        self.max_held_s = 0.0
        self._entered = 0.0

    async def acquire(self) -> bool:
        started = time.perf_counter()
        result = await super().acquire()
        now = time.perf_counter()
        self.wait_s += now - started
        self.acquisitions += 1
        self._entered = now
        return result

    def release(self) -> None:
        held = time.perf_counter() - self._entered
        self.held_s += held
        self.max_held_s = max(self.max_held_s, held)
        super().release()

    def stats(self) -> dict[str, float]:
        return {
            "acquisitions": self.acquisitions,
            "wait_s": round(self.wait_s, 6),
            "held_s": round(self.held_s, 6),
            "max_held_s": round(self.max_held_s, 6),
        }


@dataclass
class RequestRecord:
    """One model request made by one attempt."""

    attempt_id: str
    started: float
    planned_end: float
    prompt_tokens: int
    completion_tokens: int
    cancellable: bool
    cancel_requested: float | None = None
    cancel_acknowledged: float | None = None
    completed: float | None = None
    billed_prompt: int = 0
    billed_completion: int = 0
    observable: bool = True
    delivered: bool = False

    def row(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class RequestLog:
    requests: list[RequestRecord] = field(default_factory=list)

    def billed(self) -> tuple[int, int]:
        return (
            sum(item.billed_prompt for item in self.requests),
            sum(item.billed_completion for item in self.requests),
        )

    def after_cancel(self) -> int:
        """Completion tokens billed after a cancel request (modeled when not observable)."""
        total = 0
        for item in self.requests:
            if item.cancel_requested is None:
                continue
            duration = max(item.planned_end - item.started, 1e-9)
            stop = item.completed if item.completed is not None else item.cancel_requested
            frac_after = max(0.0, min(stop, item.planned_end) - item.cancel_requested) / duration
            total += int(round(item.completion_tokens * frac_after))
        return total


class _VirtualSelector(selectors.DefaultSelector):
    """Poll without blocking and advance the virtual clock by the requested timeout."""

    def __init__(self, clock: "VirtualClock") -> None:
        super().__init__()
        self._clock = clock

    def select(self, timeout: float | None = None):
        if timeout is not None and timeout > 0:
            self._clock.now += timeout
        elif timeout is None:
            self._clock.idle_spins += 1
        return super().select(0)


class VirtualClock:
    """A deterministic event loop clock.

    Sleeping advances simulated time immediately once every runnable task has
    yielded, so a run with 30-second model calls finishes in milliseconds and
    the same seed reproduces the same interleaving. A loop with nothing
    scheduled and nothing runnable is a deadlock; `idle_spins` counts those
    polls so a harness can detect it.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.idle_spins = 0

    def new_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.SelectorEventLoop(_VirtualSelector(self))
        loop.time = lambda: self.now  # type: ignore[method-assign]
        return loop

    def run(self, coro):
        with asyncio.Runner(loop_factory=self.new_loop) as runner:
            return runner.run(coro)


@contextlib.contextmanager
def wall_timer() -> Iterator[dict[str, float]]:
    out = {"s": 0.0}
    started = time.perf_counter()
    try:
        yield out
    finally:
        out["s"] = time.perf_counter() - started
