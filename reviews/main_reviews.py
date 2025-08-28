# reviews/main_reviews.py
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
main_reviews.py — многопоточный запуск парсинга отзывов Google Maps.
Модель распределения: worker-per-proxy.
- Каждый воркер (поток) получает ОДИН уникальный прокси (если прокси заданы)
  и свою порцию мест для обхода.
- Запись в CSV — под общим lock.
Совместимо с запуском как пакета: `python -m reviews.main_reviews` и как скрипта.
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

# --- поддержка запуска и как пакета, и как скрипта ---
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
    from model import Place  # type: ignore
    from scrape_reviews import (  # type: ignore
        scrape_place_reviews,
        MAX_RETRIES_PER_PLACE,
        REVIEW_LANGUAGE,
        DEBUG_SELECTORS,
        CODE_VERSION,
    )

# --- формат выходного CSV ---
OUT_HEADER = [
    "Beach ID","Place","Category","Categories",
    "Place (UI)","Place URL","Input URL","Review ID","Review URL",
    "Rating","Date","Author","Author URL","Author Photo",
    "Is Local Guide","Text","Photo URLs (list)","RawReview"
]



def load_places(csv_path: str) -> List[Place]:
    """
    Читает список мест из CSV.
    Ожидаемые поля: beach_id, place_id, name, category, categories, polygon_name, place_url (последние три — опциональные).
    Поле `categories` — JSON-массив строк (напр. ["Restaurant","Bar"]).
    """
    import json
    places: List[Place] = []

    def _parse_categories(val: Optional[str]):
        if not val:
            return None
        s = (val or "").strip()
        # Пытаемся распарсить как JSON
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                cleaned = [str(x).strip() for x in arr if str(x).strip()]
                return tuple(cleaned) if cleaned else None
        except Exception:
            pass
        # Фолбэк: вручную (если пришло типа: ["A","B"] или 'A, B')
        s2 = s.strip().strip("[]")
        if not s2:
            return None
        parts = [p.strip().strip('"').strip("'") for p in s2.split(",")]
        parts = [p for p in parts if p]
        return tuple(parts) if parts else None

    with open(csv_path, encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            beach_id = (r.get("beach_id") or r.get("Beach ID") or "").strip()
            place_id = (r.get("place_id") or r.get("Place ID") or "").strip()
            name = (r.get("name") or r.get("Place") or "").strip()
            polygon_name = (r.get("polygon_name") or r.get("Polygon") or "").strip() or None
            place_url = (r.get("place_url") or r.get("Place URL") or "").strip() or None

            # Новые поля
            category = (r.get("category") or r.get("Category") or "").strip() or None
            categories_raw = (r.get("categories") or r.get("Categories") or "").strip() or None
            categories = _parse_categories(categories_raw)

            if place_id and name:
                places.append(Place(
                    beach_id=beach_id,
                    place_id=place_id,
                    name=name,
                    polygon_name=polygon_name,
                    place_url=place_url,
                    category=category,
                    categories=categories,
                ))
            else:
                # подсветим битые строки, если DEBUG включен
                if DEBUG_SELECTORS:
                    print(f"⚠️ Пропуск строки без place_id/name: {r}")

    return places


def load_proxies(file_path: str) -> List[str]:
    """Читает список прокси по одному на строку. Поддерживаются http(s)://, socks5://, socks5h://, с логином/паролем."""
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
                writer: csv.DictWriter,
                out_path: str) -> None:
    """
    Обработка одного места: парсинг + запись результатов в CSV (под Lock).
    Переиспользуем готовую функцию scrape_place_reviews().
    """
    acc = []
    last_err: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES_PER_PLACE + 1):
        try:
            rows = scrape_place_reviews(place, proxy_url=proxy_url, debug=True)
            acc = rows
            break
        except Exception as e:
            last_err = e
            print(f"[{idx}] ERROR {place.name}: {e}")
            time.sleep(0.8 * attempt)

    if not acc:
        print(f"[{idx}] FAIL {place.name}: пусто после {MAX_RETRIES_PER_PLACE} попыток.")
        return

    # Запись в файл под блокировкой
        # запись в файл по мере поступления
    with lock:
        for r in acc:
            r2 = dict(r)

            # Добавим category/categories из Place
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

    print(f"[{idx}] {place.name}: +{len(acc)} (итого {len(acc)}) отзывов (UI lang={REVIEW_LANGUAGE})")


def run_worker(worker_id: int,
               jobs: List[Tuple[int, Place]],
               proxy_url: Optional[str],
               lock: threading.Lock,
               writer: csv.DictWriter,
               out_path: str) -> None:
    """
    Воркер обрабатывает свою порцию мест на ОДНОМ закреплённом прокси.
    jobs: список кортежей (сквозной индекс, Place).
    """
    if proxy_url:
        print(f"👤 Воркер #{worker_id}: прокси = {proxy_url}")
    else:
        print(f"👤 Воркер #{worker_id}: без прокси")
    for idx, place in jobs:
        process_one(idx, place, proxy_url, lock, writer, out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", required=True, help="CSV со списком мест (beach_id,place_id,name,...)")
    parser.add_argument("--out", dest="out", required=True, help="CSV для записи отзывов")
    parser.add_argument("--threads", type=int, default=1, help="Количество потоков")
    parser.add_argument("--proxies", default="proxies.txt",
                        help="Файл с прокси (по одному на строку, http(s)/socks5(h)://user:pass@host:port)")
    args = parser.parse_args()

    places = load_places(args.inp)
    if not places:
        print("Нет входных мест. Проверь CSV.")
        return

    proxies = load_proxies(args.proxies)
    print(f"Scraper version: {CODE_VERSION}")
    print(f"Всего мест: {len(places)}; потоков (запрошено): {args.threads}; файл вывода: {args.out}")

    # Подготовка файла вывода и синхронизация записи
    lock = threading.Lock()
    out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
    os.makedirs(out_dir, exist_ok=True)

    need_header = not os.path.exists(args.out) or os.path.getsize(args.out) == 0
    out_f = open(args.out, "a", encoding="utf-8", newline="")
    writer = csv.DictWriter(out_f, fieldnames=OUT_HEADER)
    if need_header:
        writer.writeheader()

    # Задачи со сквозной нумерацией
    tasks: List[Tuple[int, Place]] = list(enumerate(places, start=1))

    # Определяем реальное число воркеров
    wanted = max(1, int(args.threads))
    if proxies:
        workers = min(wanted, len(proxies))
        if wanted > len(proxies):
            print(f"⚠️ Запрошено потоков: {wanted}, но прокси: {len(proxies)}. "
                  f"Урежу до {workers}, чтобы каждому потоку достался уникальный прокси.")
    else:
        workers = wanted

    # Разбиваем задачи по воркерам равномерно (шаговая разбивка)
    # При workers > len(tasks) часть чанков может быть пустой — это ок.
    chunks: List[List[Tuple[int, Place]]] = [tasks[i::workers] for i in range(workers)]

    print(
        f"→ Стартую {workers} поток(а/ов). Каждому потоку закрепляю свой прокси."
        if proxies else
        f"→ Стартую {workers} поток(а/ов) без прокси."
    )

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = []
            for w_id in range(workers):
                proxy_for_worker = proxies[w_id] if proxies else None
                futs.append(ex.submit(run_worker, w_id, chunks[w_id], proxy_for_worker, lock, writer, args.out))
            for _ in as_completed(futs):
                pass
    finally:
        out_f.flush()
        out_f.close()

    print("✅ Готово.")


if __name__ == "__main__":
    main()
