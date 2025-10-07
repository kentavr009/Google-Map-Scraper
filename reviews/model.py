# reviews/model.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any


@dataclass(frozen=True, slots=True)
class Place:
    """
    DTO-model for a place.
    - slots=True: less memory, faster access
    - frozen=True: immutable and safer in multi-threading
    """
    place_id: str
    name: str
    place_url: Optional[str] = None
    polygon_name: Optional[str] = None

    # New fields from the input CSV:
    category: Optional[str] = None
    categories: Optional[Tuple[str, ...]] = None  # tuple instead of list for immutability

    def __post_init__(self) -> None:
        if not self.place_id:
            raise ValueError("Place.place_id is required")
        if not self.name:
            raise ValueError("Place.name is required")

    def resolve_url(self) -> str:
        """
        Returns the place URL. If place_url is not set, it constructs one
        using the place_id.
        """
        return self.place_url or f"https://www.google.com/maps/place/?q=place_id:{self.place_id}"

    @classmethod
    def from_csv_row(cls, r: Dict[str, Any]) -> "Place":
        """
        Unified constructor from a CSV row (supports your column aliases).
        """
        place_id = (r.get("place_id") or r.get("Place ID") or "").strip()
        name = (r.get("name") or r.get("Place") or "").strip()
        polygon_name = (r.get("polygon_name") or r.get("Polygon") or "").strip() or None
        place_url = (r.get("place_url") or r.get("Place URL") or "").strip() or None

        # These fields would also need to be parsed and passed
        category = (r.get("category") or r.get("Category") or "").strip() or None
        # Assuming _parse_categories from main_reviews.py or similar logic for Tuple[str, ...]
        # For simplicity, let's keep it as raw string here if not directly implementing _parse_categories in model
        categories_raw = (r.get("categories") or r.get("Categories") or "").strip() or None
        
        # NOTE: The _parse_categories logic exists in main_reviews.py.
        # If you want to use from_csv_row directly to create a Place object
        # with correctly parsed 'categories' tuple, you'd need to either
        # move _parse_categories here or pass the parsed value.
        # For now, I'll represent it as a raw string if not parsed directly in this model.
        # However, the current `load_places` in `main_reviews.py` handles this.
        # So, if this classmethod is strictly for a raw Dict row and not the full parsing,
        # it might need adjustment or the parsing logic for `categories` moved here.
        # For a direct translation of the provided code, `categories` isn't handled here.
        # I'll add a placeholder to reflect the `categories` field, assuming it's parsed externally
        # or `_parse_categories` is made available to this classmethod.

        # To correctly populate 'categories' and 'category' from a raw CSV row Dict,
        # you'd need the parsing logic here or ensure it's done before calling from_csv_row.
        # For a direct and self-contained classmethod, we'd add it:
        
        def _parse_categories_local(val: Optional[str]) -> Optional[Tuple[str, ...]]:
            # This is a simplified version, or you'd import it.
            if not val: return None
            try: return tuple(json.loads(val))
            except (json.JSONDecodeError, TypeError):
                s2 = val.strip().strip("[]")
                if not s2: return None
                parts = [p.strip().strip('"').strip("'") for p in s2.split(",")]
                parts = [p for p in parts if p]
                return tuple(parts) if parts else None
        
        categories = _parse_categories_local(categories_raw)

        return cls(
            place_id=place_id,
            name=name,
            place_url=place_url,
            polygon_name=polygon_name,
            category=category,
            categories=categories,
        )
