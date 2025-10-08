# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Полный сборщик отзывов Google Maps для одного Place с устойчивостью к
сетевым сбоям (прокси/туннели) и антизалипанием UI.

Ключи:
- DEFAULT_TIMEOUT_MS / DEFAULT_NAV_TIMEOUT_MS (по умолчанию 45s)
- PLACE_HARD_TIMEOUT_SEC (общий лимит времени на место)
- NO_PROGRESS_MAX_SECS (сколько секунд нет новых review-id — останавливаемся)
- Жёсткое открытие диалога «All reviews» + ребаунсы
- Перепривязка скролл-контейнера
"""

try:
    from .model import Place  # type: ignore
except Exception:
    import os as _os, sys as _sys
    _sys.path.append(_os.path.dirname(__file__))
    from placeid.reviews.model import Place  # type: ignore

from typing import List, Dict, Any, Optional, Tuple
import os, re, json, time, random
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from urllib.parse import urlparse, unquote
from playwright.sync_api import sync_playwright

CODE_VERSION = "v2025-10-03-antistuck+net-stability-full"

# ======= Конфиг =======
HEADLESS = (os.getenv("HEADLESS", "false").lower() == "true")
REVIEW_LANGUAGE = (os.getenv("REVIEW_LANGUAGE") or "en").strip()
BROWSER = (os.getenv("BROWSER") or "chromium").strip()

# Под прокси даём щедрые таймауты
DEFAULT_TIMEOUT_MS = int(os.getenv("DEFAULT_TIMEOUT_MS", "45000") or "45000")
DEFAULT_NAV_TIMEOUT_MS = int(os.getenv("DEFAULT_NAV_TIMEOUT_MS", str(DEFAULT_TIMEOUT_MS)) or str(DEFAULT_TIMEOUT_MS))

SCROLL_IDLE_ROUNDS = int(os.getenv("SCROLL_IDLE_ROUNDS", "3") or "3")
SCROLL_PAUSE_MS = int(os.getenv("SCROLL_PAUSE_MS", "1000") or "1000")
MAX_RETRIES_PER_PLACE = int(os.getenv("MAX_RETRIES_PER_PLACE", "3") or "3")
MAX_REVIEWS_PER_PLACE = int(os.getenv("MAX_REVIEWS_PER_PLACE", "0") or "0")
MAX_SCROLL_ROUNDS = int(os.getenv("MAX_SCROLL_ROUNDS", "1800") or "1800")
DEBUG_SELECTORS = os.getenv("DEBUG_SELECTORS", "1").strip().lower() in ("1","true","yes")
TRANSLATE_SWITCH = os.getenv("TRANSLATE_SWITCH", "0").strip().lower() in ("1","true","yes")
BLOCK_RESOURCES = os.getenv("BLOCK_RESOURCES", "1").strip().lower() in ("1","true","yes")
FALLBACK_TO_SIDEPANEL = os.getenv("FALLBACK_TO_SIDEPANEL", "0").strip().lower() in ("1","true","yes")
UI_LAG_TOLERANCE = int(os.getenv("UI_LAG_TOLERANCE", "3") or "3")
MIN_PLATEAU_COUNT = int(os.getenv("MIN_PLATEAU_COUNT", "20") or "20")
PLACE_HARD_TIMEOUT_SEC = int(os.getenv("PLACE_HARD_TIMEOUT_SEC", "240") or "240")
NO_PROGRESS_MAX_SECS   = int(os.getenv("NO_PROGRESS_MAX_SECS", "45") or "45")

# ======= Утилиты сети/прокси =======
def _parse_proxy_for_playwright(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
    if not proxy_url: return None
    u = urlparse(proxy_url)
    if not u.scheme or not u.hostname or not u.port:
        raise ValueError(f"Bad proxy URL: {proxy_url}")
    cfg: Dict[str, str] = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username: cfg["username"] = unquote(u.username)
    if u.password: cfg["password"] = unquote(u.password)
    return cfg

def _is_proxy_tunnel_error(msg: str) -> bool:
    s = (msg or "").lower()
    return ("err_tunnel_connection_failed" in s
            or "net::err" in s
            or ("tunnel" in s and "failed" in s))

# ======= Прочие утилиты =======
def _normalize_photo(u: str) -> str:
    m = re.search(r"/p/([^=/?]+)", u or "")
    return f"https://lh3.googleusercontent.com/p/{m.group(1)}=s0" if m else u

def _rel_to_iso(raw: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    if not raw: return None
    now = now or datetime.now(timezone.utc)
    s = (raw or "").strip().lower()
    def num(text: str, default=1) -> int:
        m = re.search(r"(\d+)", text)
        return int(m.group(1)) if m else default
    if "yesterday" in s or "вчера" in s: return (now - relativedelta(days=1)).isoformat()
    if re.search(r"\b(min|mins|minute|minutes|мин|минут[аы]?|минута)\b", s): return (now - relativedelta(minutes=num(s))).isoformat()
    if re.search(r"\b(hour|hours|час|час[аов]?)\b", s): return (now - relativedelta(hours=num(s))).isoformat()
    if re.search(r"\b(day|days|дн[ея]?)\b", s): return (now - relativedelta(days=num(s))).isoformat()
    if re.search(r"\b(week|weeks|недел[яи]|недель)\b", s): return (now - relativedelta(weeks=num(s))).isoformat()
    if re.search(r"\b(month|months|мес[яц][ацев]?)\b", s): return (now - relativedelta(months=num(s))).isoformat()
    if re.search(r"\b(year|years|год|года|лет)\b", s): return (now - relativedelta(years=num(s))).isoformat()
    return None

def _toggle_translate(page) -> None:
    if not TRANSLATE_SWITCH: return
    patterns = [
        r"Translate reviews", r"Translate to English", r"Перевести отзывы",
        r"Перевод отзывов", r"Übersetzen", r"Traducir", r"Traduire"
    ]
    for p in patterns:
        try:
            btn = page.get_by_role("button", name=re.compile(p, re.I)).first
            if btn and btn.is_visible():
                btn.click(); page.wait_for_timeout(400); return
        except Exception:
            continue

def _set_sort_newest(page) -> None:
    try:
        sort_btn = page.get_by_role("button", name=re.compile(r"(sort|сортировать|сорт|ordenar|trier)", re.I)).first
        if sort_btn and sort_btn.is_visible():
            sort_btn.click(); page.wait_for_timeout(260)
            item = page.get_by_role("menuitem", name=re.compile(r"(newest|новые|más recientes|les plus récentes)", re.I)).first
            if item and item.is_visible():
                item.click(); page.wait_for_timeout(380)
    except Exception:
        pass

def _handle_google_consent(page) -> None:
    try: page.wait_for_timeout(200)
    except Exception: pass
    try_names_reject = [r"Reject all", r"Tout refuser", r"Alle ablehnen", r"Rechazar todo", r"Rifiuta tutto",
                        r"Отклонить все", r"Hepsini reddet", r"全部拒绝", r"Afvis alle"]
    try_names_accept = [r"Accept all", r"Tout accepter", r"Alle akzeptieren", r"Aceptar todo", r"Accetta tutto",
                        r"Принять всё", r"Tümünü kabul et", r"全部接受"]
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
        if "consent.google." in (page.url or ""):
            if not _click_by_names(try_names_reject): _click_by_names(try_names_accept)
            return
        _click_by_names(try_names_reject) or _click_by_names(try_names_accept)
    except Exception:
        pass

def _click_all_reviews_if_present(page) -> bool:
    # Уже открыт диалог?
    try:
        if page.locator('[role="dialog"] div[data-review-id]').count() > 0:
            return True
    except Exception:
        pass

    selectors = [
        'button[jsaction*="pane.review.moreReviews"]',
        'button:has-text("All reviews")',
        'button:has-text("See all reviews")',
        'button:has-text("Все отзывы")',
        'button:has-text("Tous les avis")',
        'button:has-text("Alle Bewertungen")',
        'button:has-text("Todas las reseñas")',
        'button:has-text("Todas las opiniones")',
        'button:has-text("Tutte le recensioni")',
        'button:has-text("Todas as avaliações")',
    ]
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(120)
        page.keyboard.press("Home")
        page.wait_for_timeout(120)
    except Exception:
        pass
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn and btn.count() > 0 and btn.is_visible():
                btn.click()
                page.wait_for_selector('[role="dialog"] div[data-review-id], div[data-review-id]',
                                       timeout=min(9000, DEFAULT_NAV_TIMEOUT_MS))
                return True
        except Exception:
            continue
    return False

def _force_all_reviews_dialog(page) -> bool:
    for attempt in range(1, 4):
        ok = _click_all_reviews_if_present(page)
        if ok:
            return True
        try:
            tab = page.get_by_role("tab", name=re.compile(r"(reviews|отзыв|avis|bewert|reseñ|recensioni|opini[oó]es)", re.I)).first
            if tab and tab.count() > 0 and tab.is_visible():
                tab.click()
                page.wait_for_timeout(300)
                if _click_all_reviews_if_present(page):
                    return True
        except Exception:
            pass
        try:
            page.evaluate("window.scrollBy(0, 400)")
            page.wait_for_timeout(300)
        except Exception:
            pass
    try:
        if page.locator('div[data-review-id]').first.is_visible():
            return True
    except Exception:
        pass
    return False

def _is_scrollable(h) -> bool:
    try:
        return bool(h.evaluate(
            'el => !!el && (el.scrollHeight - el.clientHeight) > 40 && ["auto","scroll"].includes(getComputedStyle(el).overflowY)'
        ))
    except Exception:
        return False

def _find_reviews_container(page):
    # Предпочитаем контейнер в диалоге
    try:
        dlg = page.locator('[role="dialog"]').first
        in_dlg = dlg and dlg.count() > 0 and dlg.is_visible()
    except Exception:
        in_dlg = False

    root = '[role="dialog"]' if in_dlg else 'html'
    first = page.locator(f'{root} div[data-review-id]').first if in_dlg else page.locator('div[data-review-id]').first
    first_h = None
    try:
        if first and first.count() > 0:
            first.wait_for(state="visible", timeout=4000)
            first_h = first.element_handle()
    except Exception:
        first_h = None

    if first_h:
        try:
            cand_h = first_h.evaluate_handle("""
                el => {
                    const root = el.closest('[role="dialog"]') || document;
                    const sv = v => v==='auto'||v==='scroll';
                    let n = el;
                    while (n && root.contains(n)) {
                        const cs = getComputedStyle(n);
                        if (((n.scrollHeight - n.clientHeight) > 40) && (sv(cs.overflowY)||sv(cs.overflow))) return n;
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

    # Крупнейший scrollable с карточками
    try:
        h = page.evaluate_handle("""
            () => {
                const root = document.querySelector('[role="dialog"]') || document;
                const isVisible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                const sv = v => v === 'auto' || v === 'scroll';
                const cand = Array.from(root.querySelectorAll('div,section,main,article')).filter(el=>{
                    if (!isVisible(el)) return false;
                    const cs = getComputedStyle(el);
                    const scrollable = ((el.scrollHeight - el.clientHeight) > 40) && (sv(cs.overflowY) || sv(cs.overflow));
                    if (!scrollable) return false;
                    return el.querySelector('div[data-review-id]') !== null;
                });
                cand.sort((a,b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
                return cand[0] || null;
            }
        """)
        elem = h.as_element()
        if elem and _is_scrollable(elem):
            return elem
    except Exception:
        pass

    # Фолбэк: любой крупный scrollable
    try:
        h = page.evaluate_handle("""
            () => {
                const root = document.querySelector('[role="dialog"]') || document;
                const isVisible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
                const sv = v => v==='auto'||v==='scroll';
                const cand = Array.from(root.querySelectorAll('div,section,main,article')).filter(el=>{
                    if (!isVisible(el)) return false;
                    const cs = getComputedStyle(el);
                    return ((el.scrollHeight - el.clientHeight) > 40) && (sv(cs.overflowY)||sv(cs.overflow));
                });
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

def _defocus(page):
    try: page.evaluate("document.activeElement && document.activeElement.blur()")
    except Exception: pass

def _close_overlays(page):
    try: page.keyboard.press("Escape"); page.wait_for_timeout(60)
    except Exception: pass

def _counts_debug(page) -> Tuple[int,int,int]:
    try: a = page.locator('div[data-review-id]').count()
    except Exception: a = 0
    try: b = page.locator('div.jftiEf').count()
    except Exception: b = 0
    try: c = page.locator('.kvMYJc').count()
    except Exception: c = 0
    return a, b, c

def _list_visible_review_ids(page) -> List[str]:
    try:
        ids = page.evaluate("""
            () => {
                const out = new Set();
                document.querySelectorAll('div.jftiEf').forEach(card => {
                    const n = card.querySelector('div[data-review-id]');
                    const rid = n && n.getAttribute('data-review-id');
                    if (rid) out.add(rid);
                });
                if (out.size > 0) return Array.from(out);
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
    base = page.locator(f'div[data-review-id="{rid}"]').first
    try:
        cont = base.locator("xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' jftiEf ')][1]")
        if cont and cont.count() > 0:
            return cont.first
    except Exception:
        pass
    return base

def _scroll_step(page, container) -> None:
    _defocus(page); _close_overlays(page)
    try:
        page.evaluate("(el)=>{try{el.scrollTo(0, el.scrollTop + Math.max(700, el.clientHeight));}catch{}}", container)
    except Exception:
        pass
    page.wait_for_timeout(140)
    try:
        page.keyboard.press("End"); page.wait_for_timeout(80)
        page.keyboard.press("PageDown"); page.wait_for_timeout(60)
    except Exception:
        pass

_COORD_PAT = re.compile(r'@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)')
def _extract_lat_lng(page) -> Tuple[Optional[float], Optional[float]]:
    try:
        m = _COORD_PAT.search(page.url or "")
        if m: return float(m.group(1)), float(m.group(2))
    except Exception: pass
    try:
        hrefs = page.evaluate("""
            () => Array.from(document.querySelectorAll('[href],[data-href],[aria-label]'))
                .map(el => el.getAttribute('href') || el.getAttribute('data-href') || el.getAttribute('aria-label') || '')
                .filter(Boolean)
        """) or []
        for h in hrefs:
            m = _COORD_PAT.search(h)
            if m: return float(m.group(1)), float(m.group(2))
    except Exception: pass
    try:
        href = page.evaluate("() => window.location && window.location.href || ''") or ""
        m = _COORD_PAT.search(href)
        if m: return float(m.group(1)), float(m.group(2))
    except Exception: pass
    return None, None

# ======= Основной сбор =======
def scrape_place_reviews(place: Place, proxy_url: Optional[str] = None, debug: bool = False) -> List[Dict[str, Any]]:
    input_url = place.place_url or f"https://www.google.com/maps/place/?q=place_id:{place.place_id}"
    url = f"{input_url}&hl={REVIEW_LANGUAGE}"

    rows: List[Dict[str, Any]] = []
    seen_ids = set()
    ui_total: Optional[int] = None

    proxy_cfg = _parse_proxy_for_playwright(proxy_url)

    start_ts = time.monotonic()
    last_progress_ts = start_ts

    with sync_playwright() as pw:
        browser = context = page = None
        try:
            browser_type = getattr(pw, BROWSER)
            browser = browser_type.launch(
                headless=HEADLESS,
                proxy=proxy_cfg,
                timeout=max(DEFAULT_NAV_TIMEOUT_MS, 45000)
            )

            viewport = {"width": 1360, "height": 900} if not HEADLESS else None
            if viewport:
                context = browser.new_context(locale=REVIEW_LANGUAGE, viewport=viewport)
            else:
                context = browser.new_context(locale=REVIEW_LANGUAGE)

            page = context.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT_MS)
            try:
                context.set_default_navigation_timeout(DEFAULT_NAV_TIMEOUT_MS)
            except Exception:
                pass

            try: context.add_init_script("window.open = function(){ return null; };")
            except Exception: pass

            # Закрывать всплывающие страницы не-гугловых доменов (редиректы через прокси)
            def _close_if_not_google(p):
                try: p.wait_for_load_state("domcontentloaded", timeout=3000)
                except Exception: pass
                try: host = (urlparse(p.url).hostname or "").lower()
                except Exception: host = ""
                allow = ("google." in host or host.endswith(".google.com") or host.endswith("gstatic.com") or host.startswith("consent.google."))
                if not allow:
                    if DEBUG_SELECTORS: print(f"   [debug] popup closed: {p.url}")
                    try: p.close()
                    except Exception: pass
            try:
                page.on("popup", _close_if_not_google)
                context.on("page", _close_if_not_google)
            except Exception: pass

            if BLOCK_RESOURCES:
                def _should_block(request):
                    rt = request.resource_type
                    if rt in ("media","font","texttrack","eventsource","manifest"): return True
                    host = (urlparse(request.url).hostname or "").lower()
                    return any(b in host for b in ("doubleclick.net","googlesyndication.com","google-analytics.com","gstaticads"))
                page.route("**/*", lambda route, request: route.abort() if _should_block(request) else route.continue_())

            raw_blobs: List[Tuple[Dict[str, Any], str]] = []

            def on_resp(resp):
                try:
                    u = resp.url
                    if any(k in u for k in ("listugcposts","review/listreviews","/_/LocalReviewsUi/")):
                        try:
                            blob = resp.json()
                            try:
                                serialized = json.dumps(blob, ensure_ascii=False)
                            except Exception:
                                serialized = ""
                            raw_blobs.append((blob, serialized))
                        except Exception:
                            pass
                except Exception: pass
            page.on("response", on_resp)

            # --- Навигация с терпеливыми ретраями ---
            last_err = None
            for attempt in range(1, 4):
                try:
                    page.goto(url, wait_until="load", timeout=DEFAULT_NAV_TIMEOUT_MS)
                    if DEBUG_SELECTORS: print(f"   [debug] opened URL: {url}")
                    _handle_google_consent(page)
                    break
                except Exception as e:
                    last_err = e
                    msg = str(e)
                    if DEBUG_SELECTORS:
                        print(f"   [debug] open attempt {attempt}/3 failed: {e}")
                    if _is_proxy_tunnel_error(msg):
                        raise RuntimeError("Proxy tunnel error (ERR_TUNNEL_CONNECTION_FAILED)")
                    time.sleep(0.7 * attempt + random.uniform(0, 0.3))
            else:
                raise RuntimeError(f"Не удалось открыть страницу: {last_err}")

            opened = _force_all_reviews_dialog(page)
            if not opened and not FALLBACK_TO_SIDEPANEL:
                raise RuntimeError("Reviews UI not visible.")
            if TRANSLATE_SWITCH:
                _toggle_translate(page)
            _set_sort_newest(page)

            # Заголовок/URL места (для CSV)
            place_title_ui, place_url_ui = None, None
            try:
                for s in ['h1.DUwDvf','div.DUwDvf[role="heading"]','[role="dialog"] h1[role="heading"]','h1[aria-level="1"]','div[aria-level="1"]']:
                    el = page.locator(s).first
                    if el and el.count() > 0 and el.is_visible():
                        t = (el.inner_text() or "").strip()
                        if t: place_title_ui = t; break
                if not place_title_ui:
                    t = page.title()
                    if t: place_title_ui = re.sub(r'\s*-\s*Google Maps.*$','',t).strip() or None
                a = page.locator('a[href^="https://maps.google.com/?cid="]').first
                if a and a.count()>0:
                    href = a.get_attribute('href')
                    if href: place_url_ui = href
            except Exception: pass

            # Попробуем оценить UI total
            def _ui_reviews_total(page) -> Optional[int]:
                try:
                    vals = page.evaluate("""
                        () => {
                            const root = document.querySelector('[role="dialog"]') || document;
                            const nodes = Array.from(root.querySelectorAll('h1, h2, h3, [aria-label], [role="heading"], button, div, span')).slice(0, 1600);
                            return nodes.map(el => ({
                                t: (el.innerText || el.textContent || '').trim(),
                                aria: (el.getAttribute && el.getAttribute('aria-label')) || ''
                            }));
                        }
                    """) or []
                    patterns = [
                        r"^\s*(\d{1,3}(?:[ \.,]\d{3})*)\s*(Google\s+)?reviews?\b",
                        r"^\s*(\d{1,3}(?:[ \.,]\d{3})*)\s*reviews?\b",
                        r"^\s*(\d{1,3}(?:[ \.,]\d{3})*)\s*отзыв(?:ов|а)?\b",
                        r"^\s*(\d{1,3}(?:[ \.,]\d{3})*)\s*avis\b",
                        r"^\s*(\d{1,3}(?:[ \.,]\d{3})*)\s*bewertungen\b",
                        r"^\s*(\d{1,3}(?:[ \.,]\d{3})*)\s*reseñ[ae]s\b",
                        r"^\s*(\d{1,3}(?:[ \.,]\d{3})*)\s*recensioni\b",
                        r"^\s*(\d{1,3}(?:[ \.,]\d{3})*)\s*avaliaç(?:ões|oes)\b",
                    ]
                    def _take_num(s: str) -> Optional[int]:
                        for p in patterns:
                            m = re.search(p, s, re.I)
                            if m:
                                raw = m.group(1)
                                n = int(re.sub(r"[^\d]", "", raw))
                                if 1 <= n <= 200_000:
                                    return n
                        return None
                    for v in vals:
                        n = _take_num(v.get("aria") or "")
                        if n is not None: return n
                    for v in vals:
                        n = _take_num(v.get("t") or "")
                        if n is not None: return n
                except Exception:
                    pass
                return None

            ui_total = _ui_reviews_total(page)
            if DEBUG_SELECTORS:
                print(f"   [debug] UI reviews total: {ui_total if ui_total is not None else 'unknown'}")

            lat, lng = _extract_lat_lng(page)

            container = _find_reviews_container(page)
            if not container:
                _close_overlays(page)
                container = _find_reviews_container(page)
            if not container:
                raise RuntimeError("Не найден скролл-контейнер списка отзывов.")

            try:
                page.evaluate("(el)=>{try{el.scrollTo(0,0)}catch{}}", container)
            except Exception: pass

            # ---- Основной цикл пролистывания и парсинга ----
            idle = 0
            prev_total = 0
            rounds = 0
            prev_scroll_state: Tuple[int,int] = (-1,-1)
            no_new_rounds = 0
            rebounce_done = False  # один раз повторно жмём «All reviews», если мало карточек

            try:
                last_container_guid = container.evaluate("el => el && (el.__guid ||= Math.random().toString(36).slice(2))")
            except Exception:
                last_container_guid = None

            def _target_reached() -> bool:
                if MAX_REVIEWS_PER_PLACE and len(seen_ids) >= MAX_REVIEWS_PER_PLACE: return True
                if ui_total is not None and len(seen_ids) >= max(0, ui_total - UI_LAG_TOLERANCE): return True
                return False

            while (
                idle < SCROLL_IDLE_ROUNDS
                and rounds < MAX_SCROLL_ROUNDS
                and not _target_reached()
                and (time.monotonic() - start_ts) < PLACE_HARD_TIMEOUT_SEC
                and (time.monotonic() - last_progress_ts) < NO_PROGRESS_MAX_SECS
            ):
                if debug and DEBUG_SELECTORS:
                    a,b,c = _counts_debug(page)
                    print(f"   [debug] card counts: data-review-id:{a}; jftiEf:{b}; rating:{c}")
                    print(f"   [debug] progress: {len(seen_ids)}/{ui_total or '?'}; rounds={rounds}; idle={idle}")

                curr_ids = _list_visible_review_ids(page)
                new_ids = [rid for rid in curr_ids if rid not in seen_ids]
                if new_ids:
                    no_new_rounds = 0
                else:
                    no_new_rounds += 1

                # Если после 2 раундов всего < MIN_PLATEAU_COUNT — попробуем ещё раз открыть диалог
                if not rebounce_done and rounds >= 2 and len(seen_ids) < MIN_PLATEAU_COUNT:
                    if DEBUG_SELECTORS: print("   [debug] rebounce: try pressing All reviews again (low count)")
                    if _click_all_reviews_if_present(page):
                        cand = _find_reviews_container(page)
                        if cand and _is_scrollable(cand):
                            container = cand
                            try:
                                last_container_guid = container.evaluate("el => el && (el.__guid ||= Math.random().toString(36).slice(2))")
                            except Exception:
                                pass
                            _set_sort_newest(page)
                            idle = 0; prev_scroll_state = (-1,-1)
                            rebounce_done = True

                # Парсим карточки по новым id
                for rid in new_ids:
                    c = _card_by_id(page, rid)
                    try:
                        try: c.scroll_into_view_if_needed(timeout=1200)
                        except Exception: pass
                        try:
                            btns = c.locator(
                                'button[jsname="gxjVle"], button[jsname="fk8dgd"], '
                                'button:has-text("Read more"), button:has-text("More"), '
                                'button:has-text("Ещё"), button:has-text("Подробнее")'
                            )
                            for i in range(btns.count()):
                                b = btns.nth(i)
                                if b.is_visible(): b.click()
                        except Exception: pass

                        author = author_url = None
                        try:
                            name_span = c.locator('.d4r55').first
                            if name_span.count()>0: author = (name_span.inner_text() or "").strip() or author
                        except Exception: pass
                        if not author:
                            try:
                                author_el = c.locator('a[href*="/maps/contrib/"], button[data-href*="/maps/contrib/"]').first
                                if author_el.count()>0:
                                    author = (author_el.inner_text() or "").strip() or author
                                    url_attr = author_el.get_attribute("data-href") or author_el.get_attribute("href")
                                    if url_attr:
                                        author_url = ("https://www.google.com"+url_attr) if url_attr.startswith("/") else url_attr
                            except Exception: pass
                        if not author:
                            try:
                                av = c.locator('img[alt*="Profile photo of"], img[alt*="Фото профиля"]').first
                                alt = av.get_attribute("alt") if av and av.count() > 0 else None
                                if alt:
                                    m = re.search(r'Profile photo of (.+)', alt, re.I)
                                    if m: author = m.group(1).strip()
                            except Exception: pass

                        author_photo = None
                        try:
                            img = c.locator('img.NBa7we, img[src*="googleusercontent.com"]').first
                            if img.count()>0: author_photo = img.get_attribute("src")
                        except Exception: pass

                        rating = None
                        try:
                            r_el = c.locator('.kvMYJc, span[aria-label*="out of 5"], span[aria-label*="из 5"], span[aria-label*="звезд"]').first
                            if r_el.count()>0:
                                aria = r_el.get_attribute("aria-label") or ""
                                m = re.search(r"([\d.,]+)", aria)
                                if m: rating = float(m.group(1).replace(",", "."))
                        except Exception: pass

                        date_iso = None
                        try:
                            d_el = c.locator('.rsqaWe, span:has-text("ago"), span:has-text("назад")').first
                            if d_el.count()>0: date_iso = _rel_to_iso(d_el.text_content())
                        except Exception: pass

                        text = None
                        try:
                            for sel in ['span[jsname="bN97Pc"]','div[data-review-text]','span[class*="wiI7pd"]','span:has(> span[jsname="bN97Pc"])']:
                                el = c.locator(sel).first
                                if el.count()>0 and el.is_visible():
                                    tx = (el.text_content() or "").strip()
                                    if tx: text = tx; break
                        except Exception: pass

                        is_lg = False
                        try: is_lg = c.get_by_text(re.compile(r"(Local Guide|Местный эксперт)", re.I)).count() > 0
                        except Exception: pass

                        photos = []
                        try:
                            thumbs = c.locator('.Tya61d, [href*="lh3.googleusercontent.com"], [style*="lh3.googleusercontent.com"]')
                            for j in range(thumbs.count()):
                                t = thumbs.nth(j)
                                h = (t.get_attribute("href") or t.get_attribute("style") or "") or ""
                                m = re.search(r"https://lh3\.googleusercontent\.com/[^\s\"'()]+", h)
                                if m: photos.append(_normalize_photo(m.group(0)))
                            photos = list(dict.fromkeys(photos))
                        except Exception: pass

                        raw_match: Any = None
                        try:
                            if raw_blobs:
                                key1 = (text or "")[:32]
                                for blob, blob_serialized in raw_blobs:
                                    if rid and rid in blob_serialized:
                                        raw_match = blob
                                        break
                                    if (key1 and key1 in blob_serialized) or (
                                        author and author in blob_serialized
                                    ):
                                        raw_match = blob
                                        break
                        except Exception:
                            pass

                        rows.append({
                            "Place (UI)": place_title_ui,
                            "Place URL": input_url,
                            "Input URL": f"https://www.google.com/maps/place/?q=place_id:{place.place_id}",
                            "Lat": lat, "Lng": lng,
                            "Review ID": rid,
                            "Review URL": None,
                            "Rating": rating, "Date": date_iso,
                            "Author": author, "Author URL": author_url,
                            "Author Photo": author_photo,
                            "Is Local Guide": bool(is_lg),
                            "Text": text,
                            "Photo URLs (list)": photos,
                            "RawReview": raw_match,
                        })
                        seen_ids.add(rid)
                        last_progress_ts = time.monotonic()  # отметим прогресс

                        if MAX_REVIEWS_PER_PLACE and len(seen_ids) >= MAX_REVIEWS_PER_PLACE:
                            break
                    except Exception as e:
                        if debug and DEBUG_SELECTORS:
                            print(f"   [debug] FAILED TO PARSE CARD rid={rid}: {e}")
                        continue

                # метрики скролла до шага
                try:
                    before_top = int(page.evaluate("(el)=>el.scrollTop", container))
                    before_sh  = int(page.evaluate("(el)=>el.scrollHeight", container))
                    before_seen = len(seen_ids)
                except Exception:
                    before_top = before_sh = -1
                    before_seen = len(seen_ids)

                _scroll_step(page, container)
                try: page.wait_for_timeout(SCROLL_PAUSE_MS)
                except Exception: pass

                # оценим «рост» скролла
                try:
                    sc_top = int(page.evaluate("(el)=>el.scrollTop", container))
                    sc_h   = int(page.evaluate("(el)=>el.scrollHeight", container))
                    no_growth = (sc_top, sc_h) == prev_scroll_state
                    prev_scroll_state = (sc_top, sc_h)
                except Exception:
                    no_growth = False

                total = len(seen_ids)
                no_new_ids = (total == prev_total)

                # Если UI total неизвестен и карточек мало — плато игнорируем:
                if ui_total is None and total < MIN_PLATEAU_COUNT:
                    idle = 0
                else:
                    idle = idle + 1 if (no_new_ids and no_growth) else 0

                # Перепривязка контейнера при стагнации
                if (no_new_ids and no_growth) or (before_top == sc_top and before_sh == sc_h and total == before_seen):
                    cand = _find_reviews_container(page)
                    if cand and _is_scrollable(cand):
                        try:
                            cand_guid = cand.evaluate("el => el && (el.__guid ||= Math.random().toString(36).slice(2))")
                        except Exception:
                            cand_guid = None
                        if cand_guid and cand_guid != last_container_guid:
                            if debug and DEBUG_SELECTORS:
                                print("   [debug] rebind container & re-apply Newest")
                            container = cand
                            last_container_guid = cand_guid
                            _set_sort_newest(page)
                            idle = 0
                            prev_scroll_state = (-1,-1)

                # Целевые условия выхода
                if _target_reached():
                    break

                # Второй ребаунс — если долго нет новых id и мало карточек
                if not rebounce_done and no_new_rounds >= 3 and total < MIN_PLATEAU_COUNT:
                    if DEBUG_SELECTORS: print("   [debug] second rebounce (still low count)")
                    if _click_all_reviews_if_present(page):
                        cand = _find_reviews_container(page)
                        if cand and _is_scrollable(cand):
                            container = cand
                            try:
                                last_container_guid = container.evaluate("el => el && (el.__guid ||= Math.random().toString(36).slice(2))")
                            except Exception: pass
                            _set_sort_newest(page)
                            idle = 0; prev_scroll_state = (-1,-1)
                            rebounce_done = True

                prev_total = total
                rounds += 1

                # таймауты цикла
                if (time.monotonic() - start_ts) >= PLACE_HARD_TIMEOUT_SEC:
                    if DEBUG_SELECTORS: print(f"   [debug] stop: PLACE_HARD_TIMEOUT_SEC reached ({PLACE_HARD_TIMEOUT_SEC}s)")
                    break
                if (time.monotonic() - last_progress_ts) >= NO_PROGRESS_MAX_SECS:
                    if DEBUG_SELECTORS: print(f"   [debug] stop: NO_PROGRESS_MAX_SECS reached ({NO_PROGRESS_MAX_SECS}s) without new IDs")
                    break

            if debug and DEBUG_SELECTORS:
                print(f"   [debug] FIN: parsed={len(rows)}, ui_total={ui_total}, rounds={rounds}, idle={idle}")

        finally:
            try:
                if page: page.close()
            except Exception: pass
            try:
                if context: context.close()
            except Exception: pass
            try:
                if browser: browser.close()
            except Exception: pass

    return rows

__all__ = [
    "scrape_place_reviews",
    "MAX_RETRIES_PER_PLACE",
    "REVIEW_LANGUAGE",
    "DEBUG_SELECTORS",
    "CODE_VERSION",
]
