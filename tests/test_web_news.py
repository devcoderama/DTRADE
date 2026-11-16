"""Uji agregator berita Web UI (web/news.py) — murni, tanpa jaringan."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web import news  # noqa: E402

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Contoh</title>
    <item>
      <title>Bitcoin &lt;b&gt;naik&lt;/b&gt; lagi</title>
      <link>https://contoh.id/artikel-1</link>
      <pubDate>Mon, 16 Nov 2026 03:00:00 GMT</pubDate>
      <description>&lt;p&gt;Harga &lt;b&gt;BTC&lt;/b&gt; menguat hari ini.&lt;/p&gt;</description>
      <media:content url="https://contoh.id/gambar1.jpg" medium="image"/>
    </item>
    <item>
      <title>Artikel tanpa media</title>
      <link>https://contoh.id/artikel-2</link>
      <pubDate>Mon, 16 Nov 2026 02:00:00 GMT</pubDate>
      <description>Deskripsi polos &lt;img src="https://contoh.id/dari-desc.png"&gt; lanjut</description>
    </item>
    <item>
      <title></title>
      <link>https://contoh.id/kosong</link>
    </item>
  </channel>
</rss>
"""

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Ethereum bersiap upgrade</title>
    <link rel="alternate" href="https://contoh.id/eth-1"/>
    <published>2026-11-16T01:30:00Z</published>
    <summary>Ringkasan &lt;i&gt;penting&lt;/i&gt; tentang ETH.</summary>
  </entry>
</feed>
"""


def test_parse_rss_items_dan_media():
    items = news.parse_feed(RSS, "Contoh")
    assert len(items) == 2  # item bertitle kosong dibuang
    first = items[0]
    assert first["source"] == "Contoh"
    assert first["title"] == "Bitcoin <b>naik</b> lagi".replace("<b>", "").replace("</b>", "") or "naik" in first["title"]
    assert "<" not in first["title"] and "<" not in first["desc"]
    assert first["link"] == "https://contoh.id/artikel-1"
    assert first["img"] == "https://contoh.id/gambar1.jpg"
    assert first["ts"] > 0


def test_parse_rss_gambar_dari_deskripsi():
    items = news.parse_feed(RSS, "Contoh")
    assert items[1]["img"] == "https://contoh.id/dari-desc.png"


def test_parse_atom():
    items = news.parse_feed(ATOM, "AtomSrc")
    assert len(items) == 1
    assert items[0]["title"] == "Ethereum bersiap upgrade"
    assert items[0]["link"] == "https://contoh.id/eth-1"
    assert items[0]["ts"] > 0
    assert items[0]["img"] is None


def test_parse_rusak_kosong():
    assert news.parse_feed(b"bukan xml sama sekali", "X") == []


def test_get_news_memakai_cache(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(source, url):
        calls["n"] += 1
        return [{"source": source, "title": "t", "link": "https://x.id",
                 "ts": 1.0, "desc": "", "img": None}]

    monkeypatch.setattr(news, "_fetch_feed", fake_fetch)
    news._cache.update(ts=0.0, items=[])
    first = news.get_news(force=True)
    assert first and calls["n"] == len(news.FEEDS)
    second = news.get_news()
    assert second == first and calls["n"] == len(news.FEEDS)  # dari cache
