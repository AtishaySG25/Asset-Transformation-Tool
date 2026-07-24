"""The six target ad sizes (IAB standard)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Format:
    name: str
    width: int
    height: int

    @property
    def aspect(self) -> float:
        return self.width / self.height


FORMATS = [
    Format("300x250", 300, 250),
    Format("200x200", 200, 200),
    Format("160x600", 160, 600),
    Format("970x90", 970, 90),
    Format("728x90", 728, 90),
    Format("468x60", 468, 60),
]
