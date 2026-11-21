"""
Client-side live tools for the AI Co-Pilot:
  - /news <query>               : tries local degoog first, falls back to Google News RSS (zero API key)
  - /weather <city> | <when>    : Open-Meteo hourly forecast grouped into 6-hour windows (00, 06, 12, 18)
                                  with default target >= now + 8 hours
"""

from __future__ import annotations

from datetime import datetime, timedelta
import html
import os
import re
from typing import Any
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import requests

DEGOOG_URL = os.environ.get("DEGOOG_URL", "http://localhost:4444/api/search")
HTTP_HEADERS = {"User-Agent": "AIDrive-CoPilot/1.0"}

_GEO_CACHE: dict[str, tuple[float, float, str]] = {}

WMO_CODES: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    85: "light snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "severe thunderstorm with hail",
}

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


# =============================================================================
# /news TOOL (DEGOOG -> GOOGLE NEWS RSS FALLBACK)
# =============================================================================
def _clean_snippet(text: str, max_len: int = 160) -> str:
    clean = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:max_len].rstrip(" ,;:-")


def _search_degoog(query: str, max_items: int) -> list[str]:
    resp = requests.get(
        DEGOOG_URL,
        params={"q": query, "format": "json"},
        headers=HTTP_HEADERS,
        timeout=1.5,
    )
    resp.raise_for_status()
    data = resp.json()
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _clean_snippet(str(item.get("title") or ""), 110)
        snippet = _clean_snippet(str(item.get("snippet") or item.get("content") or ""), 130)
        if not title:
            continue
        out.append(f"{title}: {snippet}" if snippet else title)
        if len(out) >= max_items:
            break
    return out


def _search_google_news_rss(query: str, max_items: int) -> list[str]:
    q = query.strip()
    if q and q.lower() not in ("latest", "top", "today", "general", "news"):
        url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en"
    else:
        url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"

    resp = requests.get(url, headers=HTTP_HEADERS, timeout=5.0)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    out: list[str] = []
    for item in root.findall("./channel/item"):
        raw_title = (item.findtext("title") or "").strip()
        if not raw_title:
            continue
        # Strip trailing " - Publisher Name" for cleaner spoken TTS summaries
        clean_title = re.sub(r"\s+-\s+[^-]+$", "", html.unescape(raw_title)).strip()
        if clean_title and clean_title not in out:
            out.append(clean_title)
        if len(out) >= max_items:
            break
    return out


def fetch_news(query: str, max_items: int = 4) -> tuple[str, list[str]]:
    """Returns (provider_name, list_of_verified_headlines)."""
    clean_q = query.strip()
    try:
        hits = _search_degoog(clean_q or "latest news", max_items=max_items)
        if hits:
            return "degoog", hits
    except Exception:
        pass

    try:
        hits = _search_google_news_rss(clean_q, max_items=max_items)
        if hits:
            return "google_news_rss", hits
    except Exception:
        pass

    return "offline", []


# =============================================================================
# /weather TOOL (OPEN-METEO WITH 6-HOUR WINDOWS: 00, 06, 12, 18)
# =============================================================================
def _geocode_city(city: str) -> tuple[float, float, str] | None:
    key = city.strip().lower()
    if not key:
        return None
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]

    resp = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city.strip(), "count": 1, "language": "en", "format": "json"},
        headers=HTTP_HEADERS,
        timeout=5.0,
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    hit = results[0]
    label = f"{hit['name']}, {hit.get('country', '')}".strip(", ")
    coords = (float(hit["latitude"]), float(hit["longitude"]), label)
    _GEO_CACHE[key] = coords
    return coords


def _round_up_to_6h_slot(dt: datetime) -> datetime:
    """Rounds a datetime up to the next 6-hour boundary: 00:00, 06:00, 12:00, or 18:00."""
    base = dt.replace(minute=0, second=0, microsecond=0)
    if dt.minute > 0 or dt.second > 0 or dt.microsecond > 0:
        base += timedelta(hours=1)
    rem = base.hour % 6
    if rem != 0:
        base += timedelta(hours=6 - rem)
    return base


def _resolve_target_slots(when_raw: str, now: datetime) -> list[datetime]:
    """Selects which 6-hour windows (00, 06, 12, 18) to report."""
    w = when_raw.strip().lower()

    # Default: next 6-hour window at least 8 hours from now (plus the following window for context)
    if not w or w in ("default", "next", "later", "soon"):
        first = _round_up_to_6h_slot(now + timedelta(hours=8))
        return [first, first + timedelta(hours=6)]

    if w in ("now", "current", "currently"):
        cur = now.replace(minute=0, second=0, microsecond=0)
        return [cur, _round_up_to_6h_slot(now + timedelta(hours=8))]

    if w in ("tonight", "this evening", "evening", "night"):
        d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return [d0.replace(hour=18), d0 + timedelta(days=1)]

    if w == "today":
        d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        slots = [d0.replace(hour=h) for h in (6, 12, 18)] + [d0 + timedelta(days=1)]
        future = [s for s in slots if s >= now.replace(minute=0, second=0, microsecond=0)]
        return future or [d0 + timedelta(days=1)]

    if w == "tomorrow":
        d1 = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return [d1.replace(hour=6), d1.replace(hour=12), d1.replace(hour=18), d1 + timedelta(days=1)]

    if w in ("weekend", "this weekend", "next weekend"):
        days_to_sat = (5 - now.weekday()) % 7
        if days_to_sat == 0 and w == "next weekend":
            days_to_sat = 7
        sat = (now + timedelta(days=days_to_sat)).replace(hour=0, minute=0, second=0, microsecond=0)
        sun = sat + timedelta(days=1)
        return [sat.replace(hour=12), sat.replace(hour=18), sun.replace(hour=12), sun.replace(hour=18)]

    if w == "next week":
        start = (now + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
        return [start + timedelta(days=d) for d in range(4)]

    # Specific weekday (e.g. "monday", "friday")
    for day_name, day_idx in WEEKDAYS.items():
        if day_name in w:
            delta = (day_idx - now.weekday()) % 7
            target = (now + timedelta(days=delta)).replace(hour=0, minute=0, second=0, microsecond=0)
            return [
                target.replace(hour=6),
                target.replace(hour=12),
                target.replace(hour=18),
                target + timedelta(days=1),
            ]

    # Explicit ISO date YYYY-MM-DD
    m = re.search(r"(\d{4}-\d{2}-\d{2})", w)
    if m:
        try:
            target = datetime.strptime(m.group(1), "%Y-%m-%d")
            return [
                target.replace(hour=6),
                target.replace(hour=12),
                target.replace(hour=18),
                target + timedelta(days=1),
            ]
        except ValueError:
            pass

    # Fallback to minimum +8h rule
    first = _round_up_to_6h_slot(now + timedelta(hours=8))
    return [first, first + timedelta(hours=6)]


def parse_weather_args(args: str, default_city: str) -> tuple[str, str]:
    """Parses '/weather Cluj-Napoca | tonight', '/weather weekend', or '/weather Bucharest'."""
    raw = args.strip()
    if not raw:
        return default_city, "default"
    if "|" in raw:
        city_part, when_part = [p.strip() for p in raw.split("|", 1)]
        return (city_part or default_city), (when_part or "default")

    low = raw.lower()
    time_keywords = (
        "tonight", "today", "tomorrow", "weekend", "this weekend", "next weekend",
        "next week", "now", "current", "default", *WEEKDAYS.keys(),
    )
    if low in time_keywords or re.fullmatch(r"\d{4}-\d{2}-\d{2}", low):
        return default_city, low
    return raw, "default"

def fetch_weather(args: str, default_city: str = "Cluj-Napoca") -> tuple[str, list[str]]:
    """Returns (location_label, list_of_6h_window_forecast_lines)."""
    city, when = parse_weather_args(args, default_city)
    try:
        geo = _geocode_city(city)
        if geo is None:
            return city, []
        lat, lon, label = geo

        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,precipitation_probability,weather_code,wind_speed_10m",
                "timezone": "auto",
                "forecast_days": 8,
            },
            headers=HTTP_HEADERS,
            timeout=5.0,
        )
        resp.raise_for_status()
        hourly: dict[str, Any] = resp.json().get("hourly") or {}
        times: list[str] = hourly.get("time") or []
        temps = hourly.get("temperature_2m") or []
        rain_probs = hourly.get("precipitation_probability") or []
        codes = hourly.get("weather_code") or []
        winds = hourly.get("wind_speed_10m") or []

        index_by_time = {t: i for i, t in enumerate(times)}
        now = datetime.now()
        slots = _resolve_target_slots(when, now)

        now_hour = now.replace(minute=0, second=0, microsecond=0)
        lines: list[str] = []
        for slot in slots:
            key = slot.strftime("%Y-%m-%dT%H:00")
            idx = index_by_time.get(key)
            if idx is None:
                continue
            cond = WMO_CODES.get(int(codes[idx]), "mixed conditions") if idx < len(codes) else "mixed conditions"
            temp_c = round(float(temps[idx])) if idx < len(temps) else None
            rain_p = int(rain_probs[idx]) if idx < len(rain_probs) and rain_probs[idx] is not None else 0
            wind_k = round(float(winds[idx])) if idx < len(winds) and winds[idx] is not None else 0
            if abs((slot - now_hour).total_seconds()) <= 3600:
                prefix = f"Current weather right now ({slot.strftime('%H:%M')})"
            else:
                prefix = f"Future forecast for {slot.strftime('%A %H:%M')}"
            lines.append(
                f"{prefix}: {cond}, {temp_c} degrees Celsius, "
                f"{rain_p} percent rain chance, wind {wind_k} kilometers per hour"
            )
        return label, lines
    except Exception:
        return city, []