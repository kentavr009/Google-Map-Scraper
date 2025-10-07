# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

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
    from placeid.reviews.model import Place  # type: ignore
    from placeid.reviews.scrape_reviews import (  # type: ignore
        scrape_place_reviews,
        MAX_RETRIES_PER_PLACE,
        REVIEW_LANGUAGE,
        DEBUG_SELECTORS,
        CODE_VERSION,
    )

OUT_HEADER = [
    "Beach ID","Place","Category","Categories",
    "Place (UI)","Place URL","Input URL","Lat","Lng",
    "Review ID","Review URL","Rating","Date","Author","Author URL","Author Photo",
    "Is Local Guide","Text","Photo URLs (list)","RawReview"
]

# ---- простая проверка прокси через requests (быстрее, чем поднимать браузер) ----
def _probe_proxy(proxy_url: str, timeout: float = 8.0) -> bool:
    try:
        import requests
        proxies = {"http": proxy_url, "https": proxy_url}
        r = requests.get("https://www.google.com/generate_204", proxies=proxies, timeout=timeout)
        return r.status_code in (204, 200)
    except Exception:
        return False

def load_places(csv_path: str) -> List[Place]:
    places: List[Place] = []

    def _parse_categories(val: Optional[str]):
        if not val:
            return None
        s = (val or "").strip()
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                cleaned = [str(x).strip() for x in arr if str(x).strip()]
                return tuple(cleaned) if cleaned else None
        except Exception:
            pass
        s2 = s.strip().strip("[]")
        if not s2:
            return None
        parts = [p.strip().strip('"').strip("'") for p in s2.split(",")]
        parts = [p for p in parts if p]
        return tuple(parts) if parts else None

    with open(csv_path, encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            beach_id = (r.get("Beach ID") or r.get("beach_id") or "").strip() or None
            place_id = (r.get("place_id") or r.get("Place ID") or "").strip()
            name = (r.get("name") or r.get("Place") or "").strip()
            polygon_name = (r.get("polygon_name") or r.get("Polygon") or "").strip() or None
            place_url = (r.get("place_url") or r.get("Place URL") or "").strip() or None

            category = (r.get("category") or r.get("Category") or "").strip() or None
            categories_raw = (r.get("categories") or r.get("Categories") or "").strip() or None
            categories = _parse_categories(categories_raw)

            if place_id and name:
                places.append(Place(
                    place_id=place_id,
                    name=name,
                    place_url=place_url,
                    polygon_name=polygon_name,
                    category=category,
                    categories=categories,
                    beach_id=beach_id,
                ))
            else:
                if DEBUG_SELECTORS:
                    print(f"⚠️ Пропуск строки без place_id/name: {r}")

    return places

def load_proxies(file_path: str) -> List[str]:
    if not file_path:
        return []
    if not os.path.exists(file_path):
        print(f"ℹ️ Файл прокси не найден: {file_path} — работаем без прокси.")
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
                writer: csv.DictWriter) -> None:
    acc = []
    last_err: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES_PER_PLACE + 1):
        try:
            rows = scrape_place_reviews(place, proxy_url=proxy_url, debug=True)
            acc = rows
            break
        except Exception as e:
            last_err = e
            print(f"[{idx}] ERROR {place.name} (try {attempt}/{MAX_RETRIES_PER_PLACE}): {e}")
            time.sleep(min(2.5, 0.7 * attempt))

    if not acc:
        print(f"[{idx}] FAIL {place.name}: пусто после {MAX_RETRIES_PER_PLACE} попыток.")
        return

    with lock:
        for r in acc:
            r2 = dict(r)
            r2.setdefault("Beach ID", getattr(place, "beach_id", None))
            r2.setdefault("Place", getattr(place, "name", None))
            r2.setdefault("Category", getattr(place, "category", None))

            cats = getattr(place, "categories", None)
            if isinstance(cats, tuple):
                r2.setdefault("Categories", json.dumps(list(cats), ensure_ascii=False))
            elif isinstance(cats, list):
                r2.setdefault("Categories", json.dumps(cats, ensure_ascii=False))
            else:
                r2.setdefault("Categories", None)

            if isinstance(r2.get("Photo URLs (list)"), list):
                r2["Photo URLs (list)"] = json.dumps(r2["Photo URLs (list)"], ensure_ascii=False)
            if r2.get("RawReview") is not None:
                try:
                    r2["RawReview"] = json.dumps(r2["RawReview"], ensure_ascii=False)
                except Exception:
                    r2["RawReview"] = None

            writer.writerow(r2)

        try:
            sys.stdout.flush()
        except Exception:
            pass

    print(f"[{idx}] {place.name}: +{len(acc)} отзыв(ов) (UI lang={REVIEW_LANGUAGE})")

def run_worker(worker_id: int,
               jobs: List[Tuple[int, Place]],
               proxy_url: Optional[str],
               lock: threading.Lock,
               writer: csv.DictWriter,
               allow_no_proxy_fallback: bool = False) -> None:
    if proxy_url:
        ok = _probe_proxy(proxy_url)
        if not ok:
            print(f"👤 Worker #{worker_id}: прокси БИТЫЙ (предварительная проверка не прошла) → "
                  f"{'перехожу без прокси' if allow_no_proxy_fallback else 'пропущу задания'}.")
            if not allow_no_proxy_fallback:
                return
            proxy_url = None

    # Небольшой джиттер старта, чтобы не лупить провайдера лавиной соединений
    time.sleep(random.uniform(0.05, 0.6))

    print(f"👤 Worker #{worker_id}: {'прокси = ' + proxy_url if proxy_url else 'без прокси'}")
    for idx, place in jobs:
        process_one(idx, place, proxy_url, lock, writer)
        # микро-пауза, чтобы снизить шанс «ERR_TUNNEL» от провайдера
        time.sleep(random.uniform(0.15, 0.35))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", required=True, help="CSV со списком мест (Beach ID,Place ID,Place,...)")
    parser.add_argument("--out", dest="out", required=True, help="CSV для записи отзывов")
    parser.add_argument("--threads", type=int, default=1, help="Количество потоков")
    parser.add_argument("--proxies", default="proxies.txt", help="Файл с прокси (http(s)/socks5(h)://user:pass@host:port)")
    parser.add_argument("--fallback-no-proxy", action="store_true", help="Если прокси умер, воркер продолжит без прокси")
    args = parser.parse_args()

    places = load_places(args.inp)
    if not places:
        print("Нет входных мест. Проверь CSV.")
        return

    proxies = load_proxies(args.proxies)
    print(f"Scraper version: {CODE_VERSION}")
    print(f"Places: {len(places)}; workers: {args.threads}; output file: {args.out}")

    lock = threading.Lock()
    out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
    os.makedirs(out_dir, exist_ok=True)

    out_f = open(args.out, "a", encoding="utf-8", newline="")
    need_header = (out_f.tell() == 0)
    writer = csv.DictWriter(out_f, fieldnames=OUT_HEADER)
    if need_header:
        writer.writeheader()
        out_f.flush()

    def _sigint_handler(signum, frame):
        print("\n⛔ SIGINT, закрываю файл…")
        try:
            out_f.flush()
            out_f.close()
        finally:
            os._exit(1)
    try:
        signal.signal(signal.SIGINT, _sigint_handler)
    except Exception:
        pass

    tasks: List[Tuple[int, Place]] = list(enumerate(places, start=1))

    wanted = max(1, int(args.threads))
    if proxies:
        workers = min(wanted, len(proxies))
        if wanted > len(proxies):
            print(f"⚠️ Запрошено потоков: {wanted}, но прокси: {len(proxies)}. Урежу до {workers}.")
    else:
        workers = wanted

    # Практический совет: для этого провайдера начните с 3–4 воркеров.
    if workers > 4:
        print("ℹ️ Совет: уменьшите --threads до 3–4 — многие прокси-провайдеры режут параллельность.")

    chunks: List[List[Tuple[int, Place]]] = [tasks[i::workers] for i in range(workers)]
    print(f"→ Start {workers} workers{' with proxy' if proxies else ' без прокси'}.")

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = []
            for w_id in range(workers):
                proxy_for_worker = proxies[w_id] if proxies else None
                futs.append(ex.submit(run_worker, w_id, chunks[w_id], proxy_for_worker, lock, writer, args.fallback_no_proxy))
            for _ in as_completed(futs):
                pass
    finally:
        try:
            out_f.flush()
            out_f.close()
        except Exception:
            pass

    print("✅ Готово.")

if __name__ == "__main__":
    main()
