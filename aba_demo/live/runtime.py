"""Asyncio runtime: drives LiveSession ticks and fans events out to subscribers.

Frame processing runs in a worker thread (``asyncio.to_thread``) so a slow
provider never blocks the event loop or the event stream. A per-session lock
serializes every session access, so the non-thread-safe Engine is only ever
touched by one thread at a time. Events are flushed on the event loop after
each frame. History is bounded; a client that falls too far behind is
disconnected and must resume by sequence number.
"""
import asyncio
from collections import deque
import time

from .scenarios import SCENARIOS
from .session import TERMINAL_STATES, InvalidTransition, LiveSession

COMMANDS = ("open", "select_target", "start", "pause", "resume", "stop", "set_activity")


class SessionNotFound(KeyError):
    pass


class CapacityExceeded(RuntimeError):
    pass


class SubscriberOverflow(RuntimeError):
    """A subscriber queue filled up; the client must reconnect with ``after``."""


class Subscription:
    def __init__(self, runner, maxsize):
        self._runner = runner
        self._maxsize = maxsize
        self._items = deque()
        self._ready = asyncio.Event()
        self.overflowed = False

    def offer(self, event) -> None:
        if self.overflowed:
            return
        if len(self._items) >= self._maxsize:
            self.overflowed = True
            self._items.clear()
        else:
            self._items.append(event)
        self._ready.set()

    async def next(self):
        while not self._items and not self.overflowed:
            self._ready.clear()
            await self._ready.wait()
        if self.overflowed:
            raise SubscriberOverflow("Client fell behind; reconnect with the last sequence")
        return self._items.popleft()

    def close(self) -> None:
        self._runner._subscribers.discard(self)


class SessionRunner:
    def __init__(self, session: LiveSession, scenario, tick_hz=20.0, history_limit=20000,
                 subscriber_queue=2000):
        self.session = session
        self.scenario = scenario
        self.tick_interval = 1.0 / tick_hz
        self.created_wall = time.time()
        self._history = deque(maxlen=history_limit)
        self._subscribers = set()
        self._subscriber_queue = subscriber_queue
        self._task = None
        self._lock = asyncio.Lock()
        self._flush()

    @property
    def id(self) -> str:
        return self.session.session_id

    def snapshot(self) -> dict:
        return dict(self.session.snapshot(), scenario=self.scenario.describe(),
                    created_wall=self.created_wall)

    def start_background(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name=f"live-{self.id}")

    async def _run(self) -> None:
        try:
            while self.session.state not in TERMINAL_STATES:
                async with self._lock:
                    # One frame per step so events stream out even when analysis is slow.
                    processed = await asyncio.to_thread(self.session.tick, 1)
                    self._flush()
                if not processed:
                    await asyncio.sleep(self.tick_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self.session._fail("worker_failed", str(exc))
            self._flush()

    async def command(self, name: str, activity: str | None = None) -> dict:
        if name not in COMMANDS:
            raise ValueError(f"Unknown command {name!r}")
        async with self._lock:
            try:
                if name == "set_activity":
                    self.session.set_activity(activity)
                else:
                    getattr(self.session, name)()
            finally:
                self._flush()
            return self.snapshot()

    def _flush(self) -> None:
        for event in self.session.drain_events():
            self._history.append(event)
            for subscriber in tuple(self._subscribers):
                subscriber.offer(event)

    def subscribe(self, after: int = 0):
        """Return (backlog, subscription). Backlog holds retained events with sequence > after."""
        subscription = Subscription(self, self._subscriber_queue)
        self._subscribers.add(subscription)
        backlog = [event for event in self._history if event["sequence"] > after]
        return backlog, subscription

    def history_start(self) -> int:
        return self._history[0]["sequence"] if self._history else 0

    async def shutdown(self, reason: str = "server_shutdown") -> None:
        async with self._lock:
            if self.session.state not in TERMINAL_STATES and self.session.state != "stopping":
                self.session.stop(reason)
                self._flush()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class SessionManager:
    """In-memory registry for a single local operator. Not a multi-user service."""

    def __init__(self, max_sessions=3, clock=time.monotonic, tick_hz=20.0, extra_scenarios=()):
        self._scenarios = dict(SCENARIOS)
        for scenario in extra_scenarios:
            self._scenarios[scenario.id] = scenario
        self.max_sessions = max_sessions
        self.clock = clock
        self.tick_hz = tick_hz
        self._runners: dict[str, SessionRunner] = {}

    def scenarios(self) -> list:
        return [scenario.describe() for scenario in self._scenarios.values()]

    def create(self, scenario_id: str) -> SessionRunner:
        scenario = self._scenarios.get(scenario_id)
        if scenario is None:
            raise ValueError(f"Unknown scenario {scenario_id!r}")
        self._evict_finished()
        if len(self._runners) >= self.max_sessions:
            raise CapacityExceeded("Too many active sessions; stop one first")
        source, provider, recorder = scenario.build()
        runner = SessionRunner(LiveSession(source, provider, recorder, clock=self.clock), scenario,
                               tick_hz=self.tick_hz)
        self._runners[runner.id] = runner
        runner.start_background()
        return runner

    def _evict_finished(self) -> None:
        """Drop the oldest finished sessions only when capacity is needed."""
        finished = [key for key, runner in self._runners.items()
                    if runner.session.state in TERMINAL_STATES]
        for key in finished:
            if len(self._runners) < self.max_sessions:
                break
            del self._runners[key]

    def get(self, session_id: str) -> SessionRunner:
        try:
            return self._runners[session_id]
        except KeyError:
            raise SessionNotFound(session_id) from None

    async def discard(self, session_id: str) -> None:
        """End the session if needed and forget it; frees capacity immediately."""
        runner = self.get(session_id)
        await runner.shutdown("user_left")
        del self._runners[session_id]

    def list(self) -> list:
        return [runner.snapshot() for runner in self._runners.values()]

    async def shutdown(self) -> None:
        for runner in tuple(self._runners.values()):
            await runner.shutdown()


__all__ = ["COMMANDS", "CapacityExceeded", "InvalidTransition", "SessionManager",
           "SessionNotFound", "SessionRunner", "SubscriberOverflow"]
