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


def assert_data_writable(settings: Settings) -> None:
    """Fase 6-A6: saat LIVE, DATA_DIR yang tidak bisa ditulis = startup GAGAL.

    Kunci harian/baseline risiko yang tidak persisten berarti proteksi
    bisa hilang diam-diam antar restart — itu tidak boleh terjadi pada
    mode live. Mode dry-run tidak ditolak (hanya diperingatkan di
    selfcheck) karena tidak ada dana yang dipertaruhkan.
    """
    if not settings.is_live:
        return
    data_dir = Path(settings.data_dir or "data")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(
            f"DATA_DIR {data_dir} tidak dapat ditulis ({exc}) — startup LIVE ditolak: "
            "state risiko wajib persisten."
        ) from exc


def hedge_mode_active(binance) -> Optional[bool]:
    """True bila akun berada di Hedge Mode; None bila tidak bisa dibaca."""
    try:
        return bool(binance.dual_side_position().get("dualSidePosition"))
    except Exception:
        return None


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

    # 4) Direktori data & log dapat ditulis (DATA_DIR tunggal, Fase 6-A6)
    writable_ok, writable_detail = True, "data/ dan logs/ dapat ditulis."
    for d in (Path(settings.data_dir or "data"), Path("logs")):
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

    # 7) Mode posisi akun (Fase 6-A7): Hedge Mode TIDAK didukung — kode
    #    tidak pernah mengirim positionSide, jadi entri akan ditolak
    #    bursa atau berperilaku tak terduga. Live + Hedge = GAGAL.
    if allow_network and binance is not None and settings.has_binance_keys:
        dual = hedge_mode_active(binance)
        if dual is None:
            checks.append(CheckResult("Mode posisi akun", None, "tidak terbaca (endpoint mode posisi gagal)."))
        elif dual and settings.is_live:
            checks.append(CheckResult(
                "Mode posisi akun", False,
                "Hedge Mode AKTIF — bot MENOLAK trading (positionSide tidak didukung); "
                "ubah akun ke One-way Mode.",
            ))
        elif dual:
            checks.append(CheckResult(
                "Mode posisi akun", True,
                "Hedge Mode aktif — akan DITOLAK saat mode live; ubah ke One-way Mode sebelum live.",
            ))
        else:
            checks.append(CheckResult("Mode posisi akun", True, "One-way Mode — sesuai yang didukung bot."))
    else:
        checks.append(CheckResult("Mode posisi akun", None, "dilewati (tidak diminta / tanpa kunci)."))

    # 8) Konektivitas Binance (hanya atas permintaan eksplisit).
    #    Fase 6-A7: ping tak bertanda tangan diganti validasi kunci lewat
    #    panggilan BERTANDA TANGAN + pengukuran selisih jam server.
    if allow_network and binance is not None and settings.has_binance_keys:
        try:
            import time as _time

            server_ms = int(binance.server_time()["serverTime"])
            drift_ms = abs(int(_time.time() * 1000) - server_ms)
            binance.account()  # panggilan bertanda tangan: memvalidasi kunci & izin
            checks.append(CheckResult(
                "Konektivitas Binance", True,
                f"kunci valid (panggilan bertanda tangan OK); selisih jam server {drift_ms} ms"
                + (" — di atas 1000 ms, sinkronkan jam mesin." if drift_ms > 1000 else "."),
            ))
        except Exception as exc:
            checks.append(CheckResult("Konektivitas Binance", False, f"validasi kunci/jaringan gagal: {exc}"))
    else:
        checks.append(CheckResult(
            "Konektivitas Binance", None,
            "dilewati (tidak diminta / tanpa kunci).",
        ))

    return checks


def selfcheck_failed(checks: list[CheckResult]) -> bool:
    """True bila ada pemeriksaan yang gagal (ok is False)."""
    return any(c.ok is False for c in checks)
