# reviews/model.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True, slots=True)
class Place:
    """
    DTO-модель места.
    - slots=True: меньше память, быстрее доступ
    - frozen=True: иммутабельно и безопаснее в многопоточности
    """
    place_id: str
    name: str
    place_url: Optional[str] = None
    polygon_name: Optional[str] = None

    # Новые поля из входного CSV:
    category: Optional[str] = None
    categories: Optional[Tuple[str, ...]] = None  # кортеж вместо list для иммутабельности

    def __post_init__(self) -> None:
        if not self.place_id:
            raise ValueError("Place.place_id is required")
        if not self.name:
            raise ValueError("Place.name is required")

    def resolve_url(self) -> str:
        return self.place_url or f"https://www.google.com/maps/place/?q=place_id:{self.place_id}"

    # Унифицированный конструктор из CSV-строки (поддерживает твои алиасы колонок)
    @classmethod
    def from_csv_row(cls, r: Dict[str, Any]) -> "Place":
        place_id = (r.get("place_id") or r.get("Place ID") or "").strip()
        name = (r.get("name") or r.get("Place") or "").strip()
        polygon_name = (r.get("polygon_name") or r.get("Polygon") or "").strip() or None
        place_url = (r.get("place_url") or r.get("Place URL") or "").strip() or None
        return cls(
            place_id=place_id,
            name=name,
            place_url=place_url,
            polygon_name=polygon_name,
        )
