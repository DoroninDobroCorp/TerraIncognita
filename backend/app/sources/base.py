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
