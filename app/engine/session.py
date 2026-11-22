"""Conversation session storage.

`SessionStore` is the interface the router depends on. v1 ships the
`InMemorySessionStore`; v2 can swap in a Redis/SQLite implementation
without touching the router.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Session:
    """Per-user conversation state."""

    key: str
    active_flow: str | None = None
    step_index: int = 0
    data: dict[str, str] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)  # {"role": ..., "content": ...}

    def reset(self) -> None:
        """Return the session to the idle (main-menu) state, keeping history."""
        self.active_flow = None
        self.step_index = 0
        self.data = {}


class SessionStore(Protocol):
    """Storage interface for conversation sessions."""

    def get_or_create(self, key: str) -> Session:
        """Return the existing session for `key`, creating one if needed."""
        ...

    def save(self, session: Session) -> None:
        """Persist a session (no-op for in-memory, required for Redis/SQLite)."""
        ...

    def clear(self, key: str) -> None:
        """Drop the session for `key` entirely."""
        ...


class InMemorySessionStore:
    """Thread-safe-enough dict-backed store for v1 single-process deployments."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, key: str) -> Session:
        session = self._sessions.get(key)
        if session is None:
            session = Session(key=key)
            self._sessions[key] = session
        return session

    def save(self, session: Session) -> None:
        self._sessions[session.key] = session

    def clear(self, key: str) -> None:
        self._sessions.pop(key, None)
