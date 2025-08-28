# reviews/scrape_reviews.py
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scrape_reviews.py — сбор отзывов Google Maps для одного Place.
- Поддержка Playwright с прокси (http/https/socks5/socks5h + логин/пароль)
- Блокировка лишних ресурсов для скорости
- Устойчивый поиск скролл-контейнера и карточек
- Расширенный парсинг относительных дат (ru/en)
Совместим с вызовом из main_reviews.py: scrape_place_reviews(place, proxy_url, debug=True)
"""

# --- импорт Place, который работает и как пакет, и как одиночный скрипт ---
try:
    # когда запускаем модулем:  python -m reviews.main_reviews
    from .model import Place  # type: ignore
except Exception:
    import os as _os, sys as _sys
    _sys.path.append(_os.path.dirname(__file__))
    from model import Place  # type: ignore

from typing import List, Dict, Any, Optional, Tuple
import os, re, json
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from urllib.parse import urlparse, unquote
from playwright.sync_api import sync_playwright

# ===== Версия для диагностики =====
CODE_VERSION = "v2025-08-27-a"

# -------- конфиг через ENV (есть дефолты) --------
HEADLESS = (os.getenv("HEADLESS", "false").lower() == "true")
REVIEW_LANGUAGE = (os.getenv("REVIEW_LANGUAGE") or "en").strip()
BROWSER = (os.getenv("BROWSER") or "chromium").strip()   # chromium|firefox|webkit
SCROLL_IDLE_ROUNDS = int(os.getenv("SCROLL_IDLE_ROUNDS", "3") or "3")
SCROLL_PAUSE_MS = int(os.getenv("SCROLL_PAUSE_MS", "1200") or "1200")
MAX_RETRIES_PER_PLACE = int(os.getenv("MAX_RETRIES_PER_PLACE", "3") or "3")
MAX_REVIEWS_PER_PLACE = int(os.getenv("MAX_REVIEWS_PER_PLACE", "0") or "0")  # 0 = без лимита
MAX_SCROLL_ROUNDS = int(os.getenv("MAX_SCROLL_ROUNDS", "300") or "300")
DEBUG_SELECTORS = os.getenv("DEBUG_SELECTORS", "1").strip() in ("1", "true", "True")
TRANSLATE_SWITCH = os.getenv("TRANSLATE_SWITCH", "0").strip() in ("1", "true", "True")
LANG_FILTER_EN = os.getenv("LANG_FILTER_EN", "0").strip() in ("1", "true", "True")

# -------- утилиты --------
def _normalize_photo(u: str) -> str:
    m = re.search(r"/p/([^=/?]+)", u or "")
    return f"https://lh3.googleusercontent.com/p/{m.group(1)}=s0" if m else u

def _rel_to_iso(raw: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """
    Конвертирует относительные даты (ru/en) → ISO.
    Поддержка форм:
      - yesterday / вчера
      - N min/mins/мин/минут(ы)
      - N hour/hours/час/часа/часов
      - N day/days/день/дня/дней
      - N week/weeks/неделя/недели/недель
      - N month/months/месяц/месяца/месяцев
      - N year/years/год/года/лет
    """
    if not raw:
        return None
    now = now or datetime.now(timezone.utc)
    s = (raw or "").strip().lower()

    def num(text: str, default=1) -> int:
        m = re.search(r"(\d+)", text)
        return int(m.group(1)) if m else default

    # yesterday/вчера
    if "yesterday" in s or "вчера" in s:
        return (now - relativedelta(days=1)).isoformat()

    # minutes
    if re.search(r"\b(min|mins|minute|minutes|мин|минут[аы]?|минута)\b", s):
        return (now - relativedelta(minutes=num(s))).isoformat()
    # hours
    if re.search(r"\b(hour|hours|час|час[аов]?)\b", s):
        return (now - relativedelta(hours=num(s))).isoformat()
    # days
    if re.search(r"\b(day|days|дн[ея]?)\b", s):
        return (now - relativedelta(days=num(s))).isoformat()
    # weeks
    if re.search(r"\b(week|weeks|недел[яи]|недель)\b", s):
        return (now - relativedelta(weeks=num(s))).isoformat()
    # months
    if re.search(r"\b(month|months|мес[яц][ацев]?)\b", s):
        return (now - relativedelta(months=num(s))).isoformat()
    # years
    if re.search(r"\b(year|years|год|года|лет)\b", s):
        return (now - relativedelta(years=num(s))).isoformat()

    return None

def _parse_proxy_for_playwright(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
    """
    Разбирает URL прокси и возвращает словарь для Playwright:
      {"server": "<scheme>://host:port", "username": "...", "password": "..."}
    Поддержка: http(s)://, socks5://, socks5h://, с логином/паролем.
    """
    if not proxy_url:
        return None
    u = urlparse(proxy_url)
    if not u.scheme or not u.hostname or not u.port:
        raise ValueError(f"Bad proxy URL: {proxy_url}")
    cfg: Dict[str, str] = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        cfg["username"] = unquote(u.username)
    if u.password:
        cfg["password"] = unquote(u.password)
    return cfg

def _toggle_translate(page) -> None:
    if not TRANSLATE_SWITCH:
        return
    patterns = [
        r"Translate reviews", r"Translate to English", r"Перевести отзывы",
        r"Перевод отзывов", r"Übersetzen", r"Traducir", r"Traduire"
    ]
    for p in patterns:
        try:
            btn = page.get_by_role("button", name=re.compile(p, re.I)).first
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(400)
                return
        except Exception:
            continue

def _set_sort_newest(page) -> None:
    try:
        # Кнопка сортировки (на панели или в диалоге)
        sort_btn = page.get_by_role("button", name=re.compile(r"(sort|сортировать|сорт|ordenar|trier)", re.I)).first
        if sort_btn and sort_btn.is_visible():
            sort_btn.click()
            page.wait_for_timeout(260)
            item = page.get_by_role("menuitem", name=re.compile(r"(newest|новые|más recientes|les plus récentes)", re.I)).first
            if item and item.is_visible():
                item.click()
                page.wait_for_timeout(380)
    except Exception:
        pass

def _handle_google_consent(page) -> None:
    """
    Закрывает баннер согласия Google (consent.google.com) по кнопкам
    'Reject all' или 'Accept all' (покрыты несколько языков).
    Вызывать сразу после page.goto() и перед дальнейшими действиями.
    """
    try:
        # иногда редирект задерживается
        page.wait_for_timeout(200)
    except Exception:
        pass

    try_names_reject = [
        r"Reject all", r"Reject All", r"Reject",                      # en
        r"Tout refuser",                                             # fr
        r"Alle ablehnen",                                            # de
        r"Rechazar todo",                                            # es
        r"Rifiuta tutto",                                            # it
        r"Отклонить все", r"Отклонить",                              # ru
        r"Hepsini reddet",                                           # tr
        r"全部拒绝", r"拒绝全部",                                        # zh
        r"Afvis alle", r"Avvisa alla", r"Avvisa alle",               # da/sv/no (прибл.)
    ]
    try_names_accept = [
        r"Accept all", r"Accept All", r"I agree",                    # en
        r"Tout accepter",                                            # fr
        r"Alle akzeptieren",                                         # de
        r"Aceptar todo",                                             # es
        r"Accetta tutto",                                            # it
        r"Принять всё", r"Согласен",                                 # ru
        r"Tümünü kabul et",                                          # tr
        r"全部接受", r"同意",                                            # zh
    ]

    def _click_by_names(names: List[str]) -> bool:
        for n in names:
            try:
                btn = page.get_by_role("button", name=re.compile(n, re.I)).first
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_timeout(250)
                    return True
            except Exception:
                continue
        return False

    try:
        # Если на consent.google.com — обязательно попробуем клик
        if "consent.google." in (page.url or ""):
            if not _click_by_names(try_names_reject):
                _click_by_names(try_names_accept)
            return

        # Если не редиректнуло на поддомен consent, попробуем искать кнопки в текущем DOM
        _click_by_names(try_names_reject) or _click_by_names(try_names_accept)
    except Exception:
        pass


def _open_reviews_tab(page) -> bool:
    """
    Надёжно открывает раздел отзывов (панель или диалог) и ждёт появления карточек.
    """
    candidates = [
        page.get_by_role("tab", name=re.compile(r"(reviews|отзыв|\d+\s+отзыв)", re.I)).first,
        page.get_by_role("button", name=re.compile(r"(reviews|отзыв|все отзывы|all reviews|see all reviews)", re.I)).first,
        page.locator('button[jsaction*="pane.review.moreReviews"]').first,
        page.get_by_text(re.compile(r"\d+\s+(Google )?reviews", re.I)).first,
    ]
    for loc in candidates:
        try:
            if not loc:
                continue
            loc.wait_for(state="visible", timeout=3000)
            loc.click()
            page.wait_for_selector('div[data-review-id]', timeout=10000)
            page.wait_for_timeout(400)
            return True
        except Exception:
            continue
    # Может уже открыто
    try:
        if page.locator('div[data-review-id]').first.is_visible():
            return True
    except Exception:
        pass
    return False

# ---------- Вспомогательные действия с фокусом/оверлеями ----------
def _defocus(page):
    try:
        page.evaluate("document.activeElement && document.activeElement.blur()")
    except Exception:
        pass

def _focus_container(page, container):
    try:
        page.evaluate("(el)=>el && el.focus && el.focus()", container)
    except Exception:
        pass

def _close_overlays(page):
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(60)
    except Exception:
        pass

# ---------- Диагностика ----------
def _counts_debug(page) -> Tuple[int,int,int]:
    try:
        a = page.locator('div[data-review-id]').count()
    except Exception:
        a = 0
    try:
        b = page.locator('div.jftiEf').count()
    except Exception:
        b = 0
    try:
        c = page.locator('.kvMYJc').count()
    except Exception:
        c = 0
    return a, b, c

def _list_visible_review_ids(page) -> List[str]:
    """
    Возвращаем уникальные reviewId карточек, стараясь брать именно контейнерные id.
    """
    try:
        ids = page.evaluate("""
            () => {
                const out = new Set();
                // сначала пробуем контейнеры карточек
                document.querySelectorAll('div.jftiEf').forEach(card => {
                    const n = card.querySelector('div[data-review-id]');
                    const rid = n && n.getAttribute('data-review-id');
                    if (rid) out.add(rid);
                });
                if (out.size > 0) return Array.from(out);
                // фолбэк — все узлы с data-review-id
                document.querySelectorAll('div[data-review-id]').forEach(n => {
                    const rid = n.getAttribute('data-review-id');
                    if (rid) out.add(rid);
                });
                return Array.from(out);
            }
        """)
        return list(ids or [])
    except Exception:
        return []

def _card_by_id(page, rid: str):
    """
    Возвращаем ЛОКАТОР контейнера карточки по reviewId:
    берём узел с таким id и поднимаемся к ближайшему предку-карточке (.jftiEf).
    """
    base = page.locator(f'div[data-review-id="{rid}"]').first
    try:
        cont = base.locator("xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' jftiEf ')][1]")
        if cont and cont.count() > 0:
            return cont.first
    except Exception:
        pass
    return base

# ---------- Поиск скролл-контейнера ЛЕНТЫ ОТЗЫВОВ ----------
def _is_scrollable(h) -> bool:
    try:
        return bool(h.evaluate(
            'el => !!el && (el.scrollHeight - el.clientHeight) > 40 && ["auto","scroll"].includes(getComputedStyle(el).overflowY)'
        ))
    except Exception:
        return False

def _container_info(h) -> str:
    try:
        info = h.evaluate('el => ({tag: el.tagName, cls: el.className, role: el.getAttribute("role"), aria: el.getAttribute("aria-label"), sh: el.scrollHeight, ch: el.clientHeight})')
        return f"{info['tag']} role={info['role']} aria='{info['aria']}' cls='{info['cls']}' scroll={info['ch']}/{info['sh']}"
    except Exception:
        return "n/a"

def _find_reviews_container(page):
    """
    Возвращает element_handle ИМЕННО прокручиваемого контейнера ленты отзывов.
    Приоритет: dialog → [role="feed"] → ближайший scrollable-предок первой карточки → крупный scrollable DIV.
    """
    first_h = None
    try:
        first = page.locator('div[data-review-id]').first
        first.wait_for(state="visible", timeout=4000)
        first_h = first.element_handle()
    except Exception:
        first_h = None

    # 1) Диалог «All reviews»
    try:
        dlg = page.locator('[role="dialog"]').first
        if dlg and dlg.count() > 0 and dlg.is_visible():
            feed = dlg.locator('[role="feed"]').first
            if feed and feed.count() > 0 and feed.is_visible():
                fh = feed.element_handle()
                if fh and _is_scrollable(fh):
                    return fh
            if first_h:
                cand_h = first_h.evaluate_handle("""
                    el => {
                        const dlg = el.closest('[role="dialog"]');
                        const sv = v => v === 'auto' || v === 'scroll';
                        let n = el;
                        while (n && dlg && dlg.contains(n)) {
                            const cs = getComputedStyle(n);
                            if (((n.scrollHeight - n.clientHeight) > 40) && (sv(cs.overflowY) || sv(cs.overflow))) return n;
                            n = n.parentElement;
                        }
                        return null;
                    }
                """)
                elem = cand_h.as_element() if cand_h else None
                if elem and _is_scrollable(elem):
                    return elem
    except Exception:
        pass

    # 2) Боковая панель: [role="feed"]
    try:
        feed = page.locator('[role="feed"]').first
        if feed and feed.count() > 0 and feed.is_visible():
            fh = feed.element_handle()
            if fh and _is_scrollable(fh):
                return fh
    except Exception:
        pass

    # 3) Ближайший scrollable-предок первой карточки
    try:
        if first_h:
            cand_h = first_h.evaluate_handle("""
                el => {
                    const sv = v => v === 'auto' || v === 'scroll';
                    let n = el;
                    while (n) {
                        const cs = getComputedStyle(n);
                        if (((n.scrollHeight - n.clientHeight) > 40) && (sv(cs.overflowY) || sv(cs.overflow))) return n;
                        n = n.parentElement;
                    }
                    return null;
                }
            """)
            elem = cand_h.as_element() if cand_h else None
            if elem and _is_scrollable(elem):
                return elem
    except Exception:
        pass

    # 4) Фолбэк: самый большой видимый scrollable DIV (внутри диалога приоритетно)
    try:
        h = page.evaluate_handle("""
            () => {
                const isVisible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                const sv = v => v === 'auto' || v === 'scroll';
                const pull = (root) => Array.from(root.querySelectorAll('div,section,main,article')).filter(el=>{
                    if (!isVisible(el)) return false;
                    const cs = getComputedStyle(el);
                    return ((el.scrollHeight - el.clientHeight) > 40) && (sv(cs.overflowY) || sv(cs.overflow));
                });
                const dlg = document.querySelector('[role="dialog"]');
                const cand = dlg ? pull(dlg) : pull(document);
                cand.sort((a,b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
                return cand[0] || null;
            }
        """)
        elem = h.as_element()
        if elem and _is_scrollable(elem):
            return elem
    except Exception:
        pass

    return None

def _container_has_cards(page, container) -> bool:
    """Проверяем, что в контейнере действительно есть карточки отзывов (визуально)."""
    try:
        return page.evaluate(
            "(el)=>!!el && el.querySelectorAll('div[data-review-id]').length>0",
            container
        )
    except Exception:
        return False

# ---------- Скролл ----------
def _scroll_step(page, container) -> None:
    # Всегда гасим фокус и закрываем возможные оверлеи/тултипы
    _defocus(page)
    _close_overlays(page)
    try:
        if container:
            # аккуратный программный скролл без hover, чтобы не триггерить UI
            page.evaluate("(el)=>{try{el.scrollTo(0, el.scrollTop + Math.max(600, el.clientHeight));}catch{}}", container)
    except Exception:
        pass
    page.wait_for_timeout(120)

# ---------- Разворачивание текста отзыва (только внутри карточки) ----------
def _expand_review_text_in_card(card) -> None:
    try:
        btns = card.locator(
            'button[jsname="gxjVle"], button[jsname="fk8dgd"], '
            'button:has-text("Read more"), button:has-text("More"), button:has-text("Ещё"), '
            'button:has-text("Подробнее")'
        )
        count = btns.count()
        for i in range(count):
            b = btns.nth(i)
            try:
                if b.is_visible():
                    b.click()
            except Exception:
                continue
    except Exception:
        pass

# -------- основной сборщик --------
def scrape_place_reviews(place: Place, proxy_url: Optional[str] = None, debug: bool = False) -> List[Dict[str, Any]]:
    """
    Возвращает список словарей:
    'Place' | 'Place (UI)' | 'Place URL' | 'Input URL' | 'Review ID' | 'Review URL' |
    'Rating' | 'Date' | 'Author' | 'Author URL' | 'Author Photo' |
    'Is Local Guide' | 'Text' | 'Photo URLs (list)' | 'RawReview'
    """
    input_url = place.place_url or f"https://www.google.com/maps/place/?q=place_id:{place.place_id}"
    url = f"{input_url}&hl={REVIEW_LANGUAGE}"

    rows: List[Dict[str, Any]] = []
    seen_ids = set()

    proxy_cfg = _parse_proxy_for_playwright(proxy_url)

    with sync_playwright() as pw:
        browser = None
        context = None
        page = None
        try:
            browser_type = getattr(pw, BROWSER)
            browser = browser_type.launch(headless=HEADLESS, proxy=proxy_cfg)
            context = browser.new_context(locale=REVIEW_LANGUAGE)
            page = context.new_page()
            page.set_default_timeout(9000)

            # --- анти-попап предохранители ---
            # 1) Запрещаем любые window.open (чтобы внешние сайты не открывались в новых вкладках)
            try:
                context.add_init_script("""
                    (() => {
                        const _open = window.open;
                        window.open = function () { return null; };
                    })();
                """)
            except Exception:
                pass

            # 2) Если всё же появилась новая вкладка/попап — сразу закрываем, если домен не google/*
            try:
                def _close_if_not_google(p):
                    try:
                        p.wait_for_load_state("domcontentloaded", timeout=3000)
                    except Exception:
                        pass
                    try:
                        host = (urlparse(p.url).hostname or "").lower()
                    except Exception:
                        host = ""
                    allow = (
                        "google." in host or
                        host.endswith(".google.com") or
                        host.endswith("gstatic.com") or
                        host.startswith("consent.google.")
                    )
                    if not allow:
                        if DEBUG_SELECTORS:
                            print(f"   [debug] popup closed: {p.url}")
                        try:
                            p.close()
                        except Exception:
                            pass

                page.on("popup", _close_if_not_google)
                context.on("page", _close_if_not_google)
            except Exception:
                pass

            # Экономим трафик без поломки карты:
            # не блокируем stylesheet и image, иначе у GMaps всё "прыгает".
            try:
                import os
                BLOCK_RESOURCES = os.getenv("BLOCK_RESOURCES", "1").strip().lower() in ("1", "true", "yes")

                if BLOCK_RESOURCES:
                    from urllib.parse import urlparse

                    def _should_block(request):
                        rt = request.resource_type  # document, stylesheet, image, media, font, script, xhr, fetch, ...
                        if rt in ("media", "font", "texttrack", "eventsource", "manifest"):
                            return True
                        # чуть-чуть подрежем шумные трекеры (без фанатизма)
                        host = (urlparse(request.url).hostname or "").lower()
                        bad_hosts = ("doubleclick.net", "googlesyndication.com", "google-analytics.com", "gstaticads")
                        if any(b in host for b in bad_hosts):
                            return True
                        return False

                    page.route("**/*", lambda route, request:
                               route.abort() if _should_block(request) else route.continue_())
            except Exception:
                pass

            # перехват "сырых" ответов (опционально)
            raw_blobs: List[Dict[str, Any]] = []
            def on_resp(resp):
                try:
                    u = resp.url
                    if any(k in u for k in ("listugcposts", "review/listreviews", "/_/LocalReviewsUi/")):
                        try:
                            raw_blobs.append(resp.json())
                        except Exception:
                            pass
                except Exception:
                    pass
            page.on("response", on_resp)

            # открыть
            last_err = None
            for attempt in range(1, MAX_RETRIES_PER_PLACE + 1):
                try:
                    page.goto(url, wait_until="domcontentloaded")
                    _handle_google_consent(page)
                    if not _open_reviews_tab(page):
                        raise RuntimeError("Reviews tab could not be opened or no reviews visible.")
                    _toggle_translate(page)
                    _set_sort_newest(page)
                    break
                except Exception as e:
                    last_err = e
                    if debug and DEBUG_SELECTORS:
                        print(f"   [debug] open attempt {attempt}/{MAX_RETRIES_PER_PLACE} failed: {e}")
                    try:
                        page.wait_for_timeout(800)
                    except Exception:
                        pass
            else:
                raise RuntimeError(f"Не удалось открыть страницу/контейнер: {last_err}")

            # название объекта из UI
            place_title_ui, place_url_ui = None, None
            try:
                title_candidates = [
                    'h1.DUwDvf',
                    'div.DUwDvf[role="heading"]',
                    '[role="dialog"] h1[role="heading"]',
                    'h1[aria-level="1"]',
                    'div[aria-level="1"]',
                ]
                for s in title_candidates:
                    el = page.locator(s).first
                    if el and el.count() > 0 and el.is_visible():
                        t = (el.inner_text() or "").strip()
                        if t:
                            place_title_ui = t
                            break
                if not place_title_ui:
                    t = page.title()
                    if t:
                        place_title_ui = re.sub(r'\s*-\s*Google Maps.*$', '', t).strip() or None
                a = page.locator('a[href^="https://maps.google.com/?cid="]').first
                if a and a.count() > 0:
                    href = a.get_attribute('href')
                    if href:
                        place_url_ui = href
            except Exception:
                pass

            # найдём скролл-контейнер
            container = _find_reviews_container(page)
            if not container or not _container_has_cards(page, container):
                # пробуем «подчистить» и найти ещё раз
                _close_overlays(page)
                container = _find_reviews_container(page)

            if not container or not _container_has_cards(page, container):
                raise RuntimeError("Не найден скролл-контейнер списка отзывов.")

            if debug and DEBUG_SELECTORS:
                print(f"   [debug] container: {_container_info(container)}")
            try:
                page.evaluate("(el)=>{try{el.scrollTo(0,0)}catch{}}", container)
                _focus_container(page, container)
            except Exception:
                pass

            idle = 0
            prev_total = 0
            rounds = 0
            prev_scroll_state: Tuple[int,int] = (-1, -1)

            while idle < SCROLL_IDLE_ROUNDS and rounds < MAX_SCROLL_ROUNDS:
                if debug and DEBUG_SELECTORS:
                    a, b, c = _counts_debug(page)
                    uniq_ids_now = _list_visible_review_ids(page)
                    print(f"   [debug] card counts: div[data-review-id]:{a}; jftiEf:{b}; kvMYJc (rating):{c}")
                    print(f"   [debug] unique reviewIds on screen: {len(uniq_ids_now)}")

                # текущие id
                curr_ids = _list_visible_review_ids(page)
                new_ids = [rid for rid in curr_ids if rid not in seen_ids]

                # парсим только новые карточки
                for rid in new_ids:
                    c = _card_by_id(page, rid)
                    try:
                        try:
                            c.scroll_into_view_if_needed(timeout=1200)
                        except Exception:
                            pass

                        # Разворачиваем текст ОТЗЫВА только в пределах карточки
                        _expand_review_text_in_card(c)

                        # Автор + URL
                        author, author_url = None, None
                        try:
                            # неинтерактивное имя
                            name_span = c.locator('.d4r55').first
                            if name_span.count() > 0:
                                author = (name_span.inner_text() or "").strip() or author
                        except Exception:
                            pass
                        if not author:
                            try:
                                author_el = c.locator('a[href*="/maps/contrib/"], button[data-href*="/maps/contrib/"]').first
                                if author_el.count() > 0:
                                    author = (author_el.inner_text() or "").strip() or author
                                    url_attr = author_el.get_attribute("data-href") or author_el.get_attribute("href")
                                    if url_attr:
                                        author_url = ("https://www.google.com" + url_attr) if url_attr.startswith("/") else url_attr
                            except Exception:
                                pass
                        if not author:
                            try:
                                av = c.locator('img[alt*="Profile photo of"], img[alt*="Фото профиля"]').first
                                alt = av.get_attribute("alt") if av and av.count() > 0 else None
                                if alt:
                                    m = re.search(r'Profile photo of (.+)', alt, re.I)
                                    if m:
                                        author = m.group(1).strip()
                            except Exception:
                                pass

                        # Аватар
                        author_photo = None
                        try:
                            img = c.locator('img.NBa7we, img[src*="googleusercontent.com"]').first
                            if img.count() > 0:
                                author_photo = img.get_attribute("src")
                        except Exception:
                            pass

                        # Рейтинг
                        rating = None
                        try:
                            r_el = c.locator('.kvMYJc, span[aria-label*="out of 5"], span[aria-label*="из 5"], span[aria-label*="звезд"]').first
                            if r_el.count() > 0:
                                aria = r_el.get_attribute("aria-label") or ""
                                m = re.search(r"([\d.,]+)", aria)
                                if m:
                                    rating = float(m.group(1).replace(",", "."))
                        except Exception:
                            pass

                        # Дата → ISO
                        date_iso = None
                        try:
                            d_el = c.locator('.rsqaWe, span:has-text("ago"), span:has-text("назад")').first
                            if d_el.count() > 0:
                                date_iso = _rel_to_iso(d_el.text_content())
                        except Exception:
                            pass

                        # Текст
                        text = None
                        try:
                            for sel in [
                                'span[jsname="bN97Pc"]',
                                'div[data-review-text]',
                                'span[class*="wiI7pd"]',
                                'span:has(> span[jsname="bN97Pc"])'
                            ]:
                                el = c.locator(sel).first
                                if el.count() > 0 and el.is_visible():
                                    tx = (el.text_content() or "").strip()
                                    if tx:
                                        text = tx
                                        break
                        except Exception:
                            pass

                        # Local Guide
                        is_lg = False
                        try:
                            is_lg = c.get_by_text(re.compile(r"(Local Guide|Местный эксперт)", re.I)).count() > 0
                        except Exception:
                            pass

                        # Фото (URL из стилей/атрибутов)
                        photos = []
                        try:
                            thumbs = c.locator('.Tya61d, [href*="lh3.googleusercontent.com"], [style*="lh3.googleusercontent.com"]')
                            tcnt = thumbs.count()
                            for j in range(tcnt):
                                t = thumbs.nth(j)
                                h = (t.get_attribute("href") or t.get_attribute("style") or "") or ""
                                m = re.search(r"https://lh3\.googleusercontent\.com/[^\s\"'()]+", h)
                                if m:
                                    photos.append(_normalize_photo(m.group(0)))
                            photos = list(dict.fromkeys(photos))
                        except Exception:
                            pass

                        # Сопоставление сырого JSON (опционально)
                        raw_match: Any = None
                        try:
                            if raw_blobs:
                                key1 = (text or "")[:32]
                                for blob in raw_blobs:
                                    s = json.dumps(blob, ensure_ascii=False)
                                    if (key1 and key1 in s) or (author and author in s):
                                        raw_match = blob
                                        break
                        except Exception:
                            pass

                        rows.append({
                            "Place": place.name,                # из CSV
                            "Place (UI)": place_title_ui,       # из DOM
                            "Place URL": input_url,
                            "Input URL": f"https://www.google.com/maps/place/?q=place_id:{place.place_id}",
                            "Review ID": rid,
                            "Review URL": None,
                            "Rating": rating,
                            "Date": date_iso,
                            "Author": author,
                            "Author URL": author_url,
                            "Author Photo": author_photo,
                            "Is Local Guide": bool(is_lg),
                            "Text": text,
                            "Photo URLs (list)": photos,
                            "RawReview": raw_match,
                        })
                        seen_ids.add(rid)

                        if MAX_REVIEWS_PER_PLACE and len(seen_ids) >= MAX_REVIEWS_PER_PLACE:
                            if debug and DEBUG_SELECTORS:
                                print(f"   [debug] reached cap {MAX_REVIEWS_PER_PLACE}, stopping.")
                            idle = SCROLL_IDLE_ROUNDS
                            break

                    except Exception as e:
                        if debug and DEBUG_SELECTORS:
                            snippet = ""
                            try:
                                snippet = (c.inner_html() or "")[:400]
                            except Exception:
                                pass
                            print(f"   [debug] FAILED TO PARSE CARD rid={rid}: {e}\n      HTML: {snippet} ...")
                        continue

                # прокрутка
                # перед шагом запомним состояние, чтобы уметь диагностировать
                try:
                    before_top = int(page.evaluate("(el)=>el.scrollTop", container))
                    before_sh  = int(page.evaluate("(el)=>el.scrollHeight", container))
                    before_seen = len(seen_ids)
                except Exception:
                    before_top = before_sh = -1
                    before_seen = len(seen_ids)

                _scroll_step(page, container)
                try:
                    page.wait_for_timeout(SCROLL_PAUSE_MS)
                except Exception:
                    pass

                # прогресс: новые id + рост scrollHeight
                try:
                    sc_top = int(page.evaluate("(el)=>el.scrollTop", container))
                    sc_h   = int(page.evaluate("(el)=>el.scrollHeight", container))
                    no_growth = (sc_top, sc_h) == prev_scroll_state
                    prev_scroll_state = (sc_top, sc_h)
                except Exception:
                    no_growth = False

                total = len(seen_ids)
                no_new_ids = (total == prev_total)

                # Если шагаем, а топ/высота не меняются И карточек не прибавилось — возможно, это не та область → перепоиск контейнера
                if (no_new_ids and no_growth) or (
                    before_top == sc_top and before_sh == sc_h and total == before_seen
                ):
                    # попытка перепривязаться
                    cand = _find_reviews_container(page)
                    if cand and _container_has_cards(page, cand):
                        if debug and DEBUG_SELECTORS:
                            print(f"   [debug] rebind container: {_container_info(cand)}")
                        container = cand
                        # сбросим "idle", потому что контейнер поменяли
                        idle = 0
                        prev_scroll_state = (-1, -1)
                    else:
                        idle += 1
                else:
                    idle = 0

                prev_total = total
                rounds += 1

            if debug and DEBUG_SELECTORS:
                print(f"   [debug] appended rows this place: {len(rows)}")

        finally:
            # Аккуратное закрытие
            try:
                if page: page.close()
            except Exception:
                pass
            try:
                if context: context.close()
            except Exception:
                pass
            try:
                if browser: browser.close()
            except Exception:
                pass

    return rows

# экспортируем константы в main_reviews
__all__ = [
    "scrape_place_reviews",
    "MAX_RETRIES_PER_PLACE",
    "REVIEW_LANGUAGE",
    "DEBUG_SELECTORS",
    "CODE_VERSION",
]
