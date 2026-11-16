"""Agregator berita crypto untuk Web UI (RSS/Atom, pustaka standar saja).

Sumber-sumber RSS publik yang sama dengan tab Berita di aplikasi
Android D TRADE. Setiap sumber diambil terpisah — satu sumber gagal
tidak menggagalkan yang lain — dan hasilnya disimpan dalam cache
memori 5 menit agar halaman Berita tidak membanjiri penerbit.

Fungsi murni `parse_feed` dipisah dari pengambilan jaringan agar bisa
diuji tanpa koneksi (aturan proyek: tanpa panggilan jaringan di tes).
"""

from __future__ import annotations

import html
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

FEEDS: list[tuple[str, str]] = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/.rss/full/"),
    ("CryptoSlate", "https://cryptoslate.com/feed/"),
    ("NewsBTC", "https://www.newsbtc.com/feed/"),
    ("AMBCrypto", "https://ambcrypto.com/feed/"),
    ("BeInCrypto", "https://beincrypto.com/feed/"),
    ("U.Today", "https://u.today/rss"),
    ("CryptoPotato", "https://cryptopotato.com/feed/"),
    ("Blockworks", "https://blockworks.co/feed"),
]

_NS = {
    "media": "http://search.yahoo.com/mrss/",
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}
_UA = {"User-Agent": "Mozilla/5.0 (compatible; DTRADE news reader)"}
_CACHE_TTL = 300.0
_MAX_ITEMS = 90

_cache: dict = {"ts": 0.0, "items": []}
_lock = threading.Lock()

_TAG_RE = re.compile(r"<[^>]+>")
_IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _clean(text: str | None, limit: int = 280) -> str:
    if not text:
        return ""
    plain = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text))).strip()
    return plain[:limit]


def _parse_date(value: str | None) -> float:
    """Tanggal RSS (RFC 822), Atom (ISO 8601), atau dc:date → epoch detik."""
    if not value:
        return 0.0
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _image_of(item: ET.Element, desc_html: str) -> str | None:
    """Gambar artikel: media:content → media:thumbnail → enclosure → <img> deskripsi."""
    for tag in ("media:content", "media:thumbnail"):
        el = item.find(tag, _NS)
        if el is not None and el.get("url"):
            return el.get("url")
    for enc in item.findall("enclosure"):
        url = enc.get("url") or ""
        if url and ("image" in (enc.get("type") or "") or
                    url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".webp"))):
            return url
    m = _IMG_RE.search(desc_html or "")
    return m.group(1) if m else None


def _text(el: ET.Element | None) -> str:
    return "".join(el.itertext()) if el is not None else ""


def parse_feed(xml_bytes: bytes, source: str) -> list[dict]:
    """Ubah satu dokumen RSS 2.0 / Atom menjadi daftar artikel seragam."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    items: list[dict] = []

    def add(title: str, link: str, date_s: str, desc_html: str, node: ET.Element) -> None:
        title = _clean(title, 200)
        link = (link or "").strip()
        if not title or not link:
            return
        items.append({
            "source": source,
            "title": title,
            "link": link,
            "ts": _parse_date(date_s),
            "desc": _clean(desc_html),
            "img": _image_of(node, desc_html),
        })

    if root.tag.endswith("feed"):  # Atom
        for entry in root.findall("atom:entry", _NS):
            link = ""
            for lnk in entry.findall("atom:link", _NS):
                if lnk.get("rel") in (None, "alternate") and lnk.get("href"):
                    link = lnk.get("href")
                    break
            add(
                _text(entry.find("atom:title", _NS)),
                link,
                _text(entry.find("atom:published", _NS)) or
                _text(entry.find("atom:updated", _NS)),
                _text(entry.find("atom:summary", _NS)) or
                _text(entry.find("atom:content", _NS)),
                entry,
            )
    else:  # RSS 2.0 / RDF
        channel = root.find("channel") if not root.tag.endswith("channel") else root
        nodes = channel.findall("item") if channel is not None else root.findall("item")
        for it in nodes:
            add(
                _text(it.find("title")),
                _text(it.find("link")),
                _text(it.find("pubDate")) or _text(it.find("dc:date", _NS)),
                _text(it.find("description")) or
                _text(it.find("content:encoded", _NS)),
                it,
            )
    return items


def _fetch_feed(source: str, url: str) -> list[dict]:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 (daftar sumber tetap)
            return parse_feed(resp.read(), source)
    except Exception:
        return []


def get_news(force: bool = False) -> list[dict]:
    """Semua artikel (terbaru dulu), dari cache memori bila masih segar."""
    now = time.time()
    with _lock:
        if not force and _cache["items"] and now - _cache["ts"] < _CACHE_TTL:
            return list(_cache["items"])
    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for part in pool.map(lambda f: _fetch_feed(f[0], f[1]), FEEDS):
            items.extend(part)
    items.sort(key=lambda x: x["ts"], reverse=True)
    items = items[:_MAX_ITEMS]
    with _lock:
        _cache.update(ts=now, items=items)
    return list(items)
