#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from aiogram import Bot


def dump_sqlite(db_path: Path, dump_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        with dump_path.open("w", encoding="utf-8") as fp:
            for line in con.iterdump():
                fp.write(f"{line}\n")
    finally:
        con.close()


def build_backup_archive(db_path: Path, awg_conf: Path, out_dir: Path, password: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    work_dir = out_dir / f"tmp-{ts}"
    work_dir.mkdir(parents=True, exist_ok=True)

    dump_path = work_dir / "vpn_bot.sql"
    dump_sqlite(db_path, dump_path)

    conf_copy = work_dir / "awg0.conf"
    conf_copy.write_bytes(awg_conf.read_bytes())

    tar_path = out_dir / f"awg-backup-{ts}.tar.gz"
    subprocess.run(["tar", "-C", str(work_dir), "-czf", str(tar_path), "vpn_bot.sql", "awg0.conf"], check=True)
    archive_path = out_dir / f"awg-backup-{ts}.tar.gz.enc"
    subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-in", str(tar_path), "-out", str(archive_path), "-pass", f"pass:{password}"],
        check=True,
    )
    tar_path.unlink(missing_ok=True)

    subprocess.run(["rm", "-rf", str(work_dir)], check=False)
    return archive_path


async def send_to_telegram(bot_token: str, chat_id: int, archive_path: Path) -> None:
    bot = Bot(token=bot_token)
    try:
        await bot.send_document(chat_id=chat_id, document=archive_path.open("rb"), caption=f"Backup: {archive_path.name}")
    finally:
        await bot.session.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backup SQLite + AWG config")
    p.add_argument("--db-path", required=True)
    p.add_argument("--awg-conf", default="/etc/amnezia/amneziawg/awg0.conf")
    p.add_argument("--out-dir", default="/backups")
    p.add_argument("--password", required=True)
    p.add_argument("--bot-token")
    p.add_argument("--admin-chat-id", type=int)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    archive_path = build_backup_archive(Path(args.db_path), Path(args.awg_conf), Path(args.out_dir), args.password)
    if args.bot_token and args.admin_chat_id:
        asyncio.run(send_to_telegram(args.bot_token, args.admin_chat_id, archive_path))
    print(archive_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
