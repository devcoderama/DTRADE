"""Kunci berkas lintas proses yang sederhana (Fase 6-B4).

Memakai pembuatan berkas eksklusif (O_CREAT|O_EXCL) — tanpa dependensi
baru. Sifatnya best-effort: bila kunci tidak didapat dalam `timeout`,
konteks tetap berjalan dengan acquired=False dan pemanggil memutuskan
(menulis state dengan peringatan lebih baik daripada crash). Berkas
kunci yang basi (proses mati) dibersihkan oleh batas waktu proses lain
hanya bila umurnya melewati `stale_after` detik.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def file_lock(path, timeout: float = 5.0, stale_after: float = 30.0) -> Iterator[bool]:
    lock_path = Path(str(path) + ".lock")
    fd = None
    deadline = time.time() + timeout
    while True:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # Kunci basi dari proses yang mati → rebut kembali.
            try:
                if time.time() - lock_path.stat().st_mtime > stale_after:
                    lock_path.unlink()
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                break
            time.sleep(0.05)
        except OSError:
            break
    try:
        yield fd is not None
    finally:
        if fd is not None:
            os.close(fd)
            try:
                lock_path.unlink()
            except OSError:
                pass
