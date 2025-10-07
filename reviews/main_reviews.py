# reviews/main_reviews.py
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
main_reviews.py — Multi-threaded execution for Google Maps review scraping.
Distribution model: worker-per-proxy.
- Each worker (thread) receives ONE unique proxy (if proxies are provided)
  and its share of places to process.
- CSV writing is performed under a shared lock.
Compatible with running as a package: `python -m reviews.main_reviews` and as a script.
"""

import argparse
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

# --- Support for running as both a package and a script ---
try:
    from .model import Place
    from .scrape_reviews import (
        scrape_place_reviews,
        MAX_RETRIES_PER_PLACE,
        REVIEW_LANGUAGE,
        DEBUG_SELECTORS,
        CODE_VERSION,
    )
except ImportError:
    # Fallback for direct script execution
    from model import Place  # type: ignore
    from scrape_reviews import (  # type: ignore
        scrape_place_reviews,
        MAX_RETRIES_PER_PLACE,
        REVIEW_LANGUAGE,
        DEBUG_SELECTORS,
        CODE_VERSION,
    )

# --- Output CSV format ---
OUT_HEADER = [
    "Place", "Category", "Categories",
    "Place (UI)", "Place URL", "Input URL", "Review ID", "Review URL",
    "Rating", "Date", "Author", "Author URL", "Author Photo",
    "Is Local Guide", "Text", "Photo URLs (list)", "RawReview"
]


def load_places(csv_path: str) -> List[Place]:
    """
    Reads a list of places from a CSV file.
    Expected fields: place_id, name, category, categories, polygon_name, place_url (last three are optional).
    The `categories` field is a JSON array of strings (e.g., ["Restaurant","Bar"]).
    """
    places: List[Place] = []

    def _parse_categories(val: Optional[str]) -> Optional[Tuple[str, ...]]:
        if not val:
            return None
        s = (val or "").strip()
        # Attempt to parse as JSON
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                cleaned = [str(x).strip() for x in arr if str(x).strip()]
                return tuple(cleaned) if cleaned else None
        except Exception:
            pass
        # Fallback: manual parsing (if input is like: ["A","B"] or 'A, B')
        s2 = s.strip().strip("[]")
        if not s2:
            return None
        parts = [p.strip().strip('"').strip("'") for p in s2.split(",")]
        parts = [p for p in parts if p]
        return tuple(parts) if parts else None

    with open(csv_path, encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            place_id = (r.get("place_id") or r.get("Place ID") or "").strip()
            name = (r.get("name") or r.get("Place") or "").strip()
            polygon_name = (r.get("polygon_name") or r.get("Polygon") or "").strip() or None
            place_url = (r.get("place_url") or r.get("Place URL") or "").strip() or None

            # New fields
            category = (r.get("category") or r.get("Category") or "").strip() or None
            categories_raw = (r.get("categories") or r.get("Categories") or "").strip() or None
            categories = _parse_categories(categories_raw)

            if place_id and name:
                places.append(Place(
                    place_id=place_id,
                    name=name,
                    polygon_name=polygon_name,
                    place_url=place_url,
                    category=category,
                    categories=categories,
                ))
            else:
                # Highlight malformed rows if DEBUG is enabled
                if DEBUG_SELECTORS:
                    print(f"⚠️ Skipping row without place_id/name: {r}")

    return places


def load_proxies(file_path: str) -> List[str]:
    """Reads a list of proxies, one per line. Supports http(s)://, socks5://, socks5h://, with username/password."""
    if not file_path:
        return []
    if not os.path.exists(file_path):
        print(f"ℹ️ Proxy file not found: {file_path} — proceeding without proxies.")
        return []
    proxies: List[str] = []
    with open(file_path, encoding="utf-8") as f:
        for line in f:
            s = (line or "").strip()
            if not s or s.startswith("#"):
                continue
            proxies.append(s)
    return proxies


def process_one(idx: int,
                place: Place,
                proxy_url: Optional[str],
                lock: threading.Lock,
                writer: csv.DictWriter,
                out_path: str) -> None:
    """
    Processes a single place: scrapes reviews + writes results to CSV (under a Lock).
    Reuses the existing scrape_place_reviews() function.
    """
    collected_rows = []
    last_err: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES_PER_PLACE + 1):
        try:
            rows = scrape_place_reviews(place, proxy_url=proxy_url, debug=True)
            collected_rows = rows
            break
        except Exception as e:
            last_err = e
            print(f"[{idx}] ERROR {place.name}: {e}")
            time.sleep(0.8 * attempt)

    if not collected_rows:
        print(f"[{idx}] FAIL {place.name}: No data after {MAX_RETRIES_PER_PLACE} attempts.")
        return

    # Write to file under a lock
    with lock:
        for r in collected_rows:
            row_to_write = dict(r)

            # Add category/categories from Place object
            row_to_write.setdefault("Category", getattr(place, "category", None))
            cats = getattr(place, "categories", None)
            if isinstance(cats, tuple):
                row_to_write.setdefault("Categories", json.dumps(list(cats), ensure_ascii=False))
            elif isinstance(cats, list):
                row_to_write.setdefault("Categories", json.dumps(cats, ensure_ascii=False))
            else:
                row_to_write.setdefault("Categories", None)

            if isinstance(row_to_write.get("Photo URLs (list)"), list):
                row_to_write["Photo URLs (list)"] = json.dumps(row_to_write["Photo URLs (list)"], ensure_ascii=False)
            if row_to_write.get("RawReview") is not None:
                try:
                    row_to_write["RawReview"] = json.dumps(row_to_write["RawReview"], ensure_ascii=False)
                except Exception:
                    row_to_write["RawReview"] = None

            writer.writerow(row_to_write)

        try:
            sys.stdout.flush()
        except Exception:
            pass

    print(f"[{idx}] {place.name}: +{len(collected_rows)} reviews (total {len(collected_rows)}) (UI lang={REVIEW_LANGUAGE})")


def run_worker(worker_id: int,
               jobs: List[Tuple[int, Place]],
               proxy_url: Optional[str],
               lock: threading.Lock,
               writer: csv.DictWriter,
               out_path: str) -> None:
    """
    A worker processes its share of places using ONE dedicated proxy.
    jobs: list of tuples (sequential index, Place object).
    """
    if proxy_url:
        print(f"👤 Worker #{worker_id}: proxy = {proxy_url}")
    else:
        print(f"👤 Worker #{worker_id}: no proxy")
    for idx, place in jobs:
        process_one(idx, place, proxy_url, lock, writer, out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", required=True, help="CSV with a list of places (beach_id,place_id,name,...)")
    parser.add_argument("--out", dest="out", required=True, help="CSV file to write reviews to")
    parser.add_argument("--threads", type=int, default=1, help="Number of threads")
    parser.add_argument("--proxies", default="proxies.txt",
                        help="File with proxies (one per line, http(s)/socks5(h)://user:pass@host:port)")
    args = parser.parse_args()

    places = load_places(args.inp)
    if not places:
        print("No input places found. Please check your CSV file.")
        return

    proxies = load_proxies(args.proxies)
    print(f"Scraper version: {CODE_VERSION}")
    print(f"Total places: {len(places)}; threads (requested): {args.threads}; output file: {args.out}")

    # Prepare output file and synchronize writing
    lock = threading.Lock()
    out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
    os.makedirs(out_dir, exist_ok=True)

    need_header = not os.path.exists(args.out) or os.path.getsize(args.out) == 0
    out_f = open(args.out, "a", encoding="utf-8", newline="")
    writer = csv.DictWriter(out_f, fieldnames=OUT_HEADER)
    if need_header:
        writer.writeheader()

    # Tasks with sequential numbering
    tasks: List[Tuple[int, Place]] = list(enumerate(places, start=1))

    # Determine the actual number of workers
    wanted_threads = max(1, int(args.threads))
    if proxies:
        workers = min(wanted_threads, len(proxies))
        if wanted_threads > len(proxies):
            print(f"⚠️ Requested threads: {wanted_threads}, but proxies available: {len(proxies)}. "
                  f"Limiting to {workers} so each thread gets a unique proxy.")
    else:
        workers = wanted_threads

    # Distribute tasks evenly among workers (stride distribution)
    # If workers > len(tasks), some chunks might be empty — this is acceptable.
    chunks: List[List[Tuple[int, Place]]] = [tasks[i::workers] for i in range(workers)]

    print(
        f"→ Starting {workers} worker(s). Each worker is assigned its own proxy."
        if proxies else
        f"→ Starting {workers} worker(s) without proxies."
    )

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = []
            for w_id in range(workers):
                proxy_for_worker = proxies[w_id] if proxies else None
                # Ensure w_id is used for proxy indexing, not just 0. It should be w_id % len(proxies)
                # if there are more workers than proxies, but the logic above ensures workers <= len(proxies)
                futs.append(ex.submit(run_worker, w_id, chunks[w_id], proxy_for_worker, lock, writer, args.out))
            for _ in as_completed(futures):
                pass
    finally:
        out_f.flush()
        out_f.close()

    print("✅ Done.")


if __name__ == "__main__":
    main()
