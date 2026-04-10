from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class Phase(str, Enum):
    INIT_DB = "init_db"
    SPOTIFY_SYNC = "spotify_sync"
    RESCAN = "rescan"
    DOWNLOAD = "download"
    TAG = "tag"
    EXPORT = "export"


@dataclass(frozen=True)
class Event:
    """A UI-friendly event emitted by the sync engine."""

    type: str
    message: str = ""
    phase: Optional[Phase] = None
    current: Optional[int] = None
    total: Optional[int] = None
    data: Optional[dict[str, Any]] = None
