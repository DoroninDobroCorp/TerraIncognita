"""Abstract base for all discovery data sources."""

from __future__ import annotations

import abc

from app.models.place import Place


class BaseSource(abc.ABC):
    """All discovery sources implement this interface."""

    @property
    @abc.abstractmethod
    def source_name(self) -> str: ...

    @abc.abstractmethod
    async def search(
        self, lat: float, lng: float, radius_km: float
    ) -> list[Place]:
        """Return places found near the given point within radius."""
        ...


class SourceError(RuntimeError):
    """An *expected* failure of an upstream source (outage, rate limit, block, malformed answer).

    Discovery logs these as one-line warnings; anything else escaping a source is a bug and gets
    a traceback.
    """


class PartialSourceResult(Exception):  # noqa: N818 - not an error: it carries usable data
    """A source answered, but only part of what it should have (e.g. a failed query tier).

    Carries the places that did arrive so discovery can use them and report the source as
    ``"partial"`` instead of a misleading ``"ok"``.
    """

    def __init__(self, places: list[Place], reason: str) -> None:
        super().__init__(reason)
        self.places = places
        self.reason = reason
