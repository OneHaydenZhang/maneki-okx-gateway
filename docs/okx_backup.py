#!/usr/bin/env python3
"""Online SQLite backup of the OKX instance (core app.db + gateway.db) into
~/backups-okx/<UTC stamp>/, keeping 14 days. Uses sqlite3's backup API, so a
running service and WAL mode are fine. Never touches ~/vectora (preview) data."""
import shutil, sqlite3, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = Path.home() / "backups-okx"
KEEP_DAYS = 14

def backup(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(src)) as s, sqlite3.connect(str(dst)) as d:
        s.backup(d)

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
for rel in ("data/auto_service/app.db", "data/okx_gateway/gateway.db"):
    src = ROOT / rel
    if src.exists():
        backup(src, DEST / stamp / src.name)
cutoff = time.time() - KEEP_DAYS * 86400
for d in DEST.iterdir() if DEST.exists() else []:
    if d.is_dir() and d.stat().st_mtime < cutoff:
        shutil.rmtree(d, ignore_errors=True)
print(f"okx backup -> {DEST / stamp}")
