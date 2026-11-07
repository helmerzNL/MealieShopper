"""Een synchrone sqlite3-verbinding voor werk dat als geheel in een thread loopt (`asyncio.to_thread`), nooit voor de event loop.

De app zelf gebruikt aiosqlite (`auth.connect`). Alleen de fasen van de back-up (backup.py) en van het prijsarchief
(price_archive.py, price_archive_export.py) lezen of schrijven zoveel regels in een transactie dat ze in een thread lopen, met
een eigen verbinding. De tabellen bestaan dan al: wie de thread start, maakt eerst het schema op de event loop
(`backup.ensure_schemas`, `price_archive.ensure_schema`).
"""

import sqlite3

from . import auth


def connect() -> sqlite3.Connection:
    """Een eigen verbinding met de database, met `sqlite3.Row` als rij. Sluit hem zelf (`contextlib.closing`)."""
    auth.data_dir().mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection = sqlite3.connect(auth.db_path())
    conn.row_factory = sqlite3.Row
    return conn
