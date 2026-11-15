"""Selfcheck — diagnosis mandiri konfigurasi & kesiapan sistem (Fase 5).

Menjawab pertanyaan "apakah instalasi ini siap jalan?" tanpa menebak:
konfigurasi valid, mode trading, kunci, direktori data, database, dan
provider AI. Pemeriksaan jaringan ke Binance hanya dilakukan bila
diminta eksplisit (`allow_network=True`) dan kunci tersedia — perintah
CLI `selfcheck` mengaktifkannya karena memang dijalankan pengguna di
mesinnya sendiri sebagai alat diagnosis.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config.settings import Settings
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class CheckResult:
    name: str
    ok: Optional[bool]  # None = dilewati / tidak berlaku
    detail: str


def run_selfcheck(
    settings: Settings,
    binance=None,
    database=None,
    ai=None,
    allow_network: bool = False,
) -> list[CheckResult]:
    checks: list[CheckResult] = []

    # 1) Konfigurasi dasar — Settings.__post_init__ sudah memvalidasi
    #    rentang saat konstruksi; di sini kita laporkan ringkasannya.
    checks.append(CheckResult(
        "Konfigurasi", True,
        f"interval {settings.default_interval}, skor v{settings.scoring_version}, "
        f"risiko {settings.risk_per_trade_pct}%/trade, maks leverage {settings.max_leverage}x",
    ))

    # 2) Mode trading — live tanpa token web adalah kombinasi berbahaya;
    #    server sendiri menolak start (Fase 1), selfcheck memperingatkan dini.
    if settings.is_live:
        if not settings.web_api_token and not settings.allow_insecure_web:
            checks.append(CheckResult(
                "Mode trading", False,
                "LIVE tetapi WEB_API_TOKEN kosong — web server akan MENOLAK start (Fase 1).",
            ))
        else:
            checks.append(CheckResult(
                "Mode trading", True,
                "LIVE — dana nyata. Pastikan ini disengaja dan forward-test sudah cukup.",
            ))
    else:
        checks.append(CheckResult(
            "Mode trading", True,
            f"dry_run ({'testnet' if settings.binance_testnet else 'mainnet'}) — aman untuk pengujian.",
        ))

    # 3) Kunci Binance
    if settings.has_binance_keys:
        checks.append(CheckResult("Kunci Binance", True, "API key & secret terisi di .env."))
    else:
        checks.append(CheckResult(
            "Kunci Binance", False,
            "BINANCE_API_KEY/SECRET kosong — fitur akun & trading tidak dapat berjalan.",
        ))

    # 4) Direktori data & log dapat ditulis
    writable_ok, writable_detail = True, "data/ dan logs/ dapat ditulis."
    for d in (Path("data"), Path("logs")):
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".selfcheck"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            writable_ok, writable_detail = False, f"{d}/ tidak dapat ditulis: {exc}"
            break
    checks.append(CheckResult("Direktori data", writable_ok, writable_detail))

    # 5) Database (opsional)
    if database is None:
        checks.append(CheckResult("Database", None, "DATABASE_URL tidak diatur — jurnal lokal (JSONL) dipakai."))
    else:
        try:
            health = database.health()
            checks.append(CheckResult(
                "Database", bool(health.get("ok")),
                "terhubung." if health.get("ok") else f"tidak terhubung: {health.get('error', 'tidak diketahui')}",
            ))
        except Exception as exc:
            checks.append(CheckResult("Database", False, f"pemeriksaan gagal: {exc}"))

    # 6) Provider AI (tanpa panggilan jaringan)
    if ai is None:
        checks.append(CheckResult("Provider AI", None, "tidak dikonfigurasi — analisis murni teknikal."))
    else:
        configured = False
        try:
            configured = bool(ai.is_configured())
        except Exception:
            configured = False
        checks.append(CheckResult(
            "Provider AI", configured,
            f"{getattr(ai, 'name', 'ai')}:{getattr(ai, 'model', '?')} terkonfigurasi."
            if configured else "provider dipilih tetapi kuncinya kosong.",
        ))

    # 7) Konektivitas Binance (hanya atas permintaan eksplisit)
    if allow_network and binance is not None and settings.has_binance_keys:
        try:
            alive = bool(binance.ping())
            checks.append(CheckResult(
                "Konektivitas Binance", alive,
                "endpoint waktu bursa merespons." if alive else "ping bursa gagal.",
            ))
        except Exception as exc:
            checks.append(CheckResult("Konektivitas Binance", False, f"ping gagal: {exc}"))
    else:
        checks.append(CheckResult(
            "Konektivitas Binance", None,
            "dilewati (tidak diminta / tanpa kunci).",
        ))

    return checks


def selfcheck_failed(checks: list[CheckResult]) -> bool:
    """True bila ada pemeriksaan yang gagal (ok is False)."""
    return any(c.ok is False for c in checks)
