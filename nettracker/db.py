"""
SQLite storage layer for NetTracker.
Stores time-series bandwidth data in a ring buffer (configurable retention).
"""

import sqlite3
import time
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def get_db_path() -> Path:
    import os
    env_path = os.environ.get("NETTRACKER_DB_PATH")
    if env_path:
        return Path(env_path)
    # Default: store in the project directory
    return Path(__file__).parent.parent / "nettracker.db"


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Return a thread-safe SQLite connection with WAL mode enabled."""
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS interface_stats (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        REAL    NOT NULL,
            iface     TEXT    NOT NULL,
            rx_bytes  INTEGER NOT NULL,
            tx_bytes  INTEGER NOT NULL,
            rx_rate   REAL    NOT NULL DEFAULT 0,
            tx_rate   REAL    NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_iface_ts
            ON interface_stats (iface, ts DESC);

        CREATE TABLE IF NOT EXISTS container_stats (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            REAL    NOT NULL,
            container_id  TEXT    NOT NULL,
            name          TEXT    NOT NULL,
            image         TEXT    NOT NULL,
            rx_bytes      INTEGER NOT NULL,
            tx_bytes      INTEGER NOT NULL,
            rx_rate       REAL    NOT NULL DEFAULT 0,
            tx_rate       REAL    NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_container_ts
            ON container_stats (container_id, ts DESC);
    """)
    conn.commit()
    logger.info("Database initialized at %s", get_db_path())


def insert_interface_stats(
    conn: sqlite3.Connection,
    records: List[Dict[str, Any]],
) -> None:
    """Batch-insert interface stats records."""
    if not records:
        return
    conn.executemany(
        """
        INSERT INTO interface_stats (ts, iface, rx_bytes, tx_bytes, rx_rate, tx_rate)
        VALUES (:ts, :iface, :rx_bytes, :tx_bytes, :rx_rate, :tx_rate)
        """,
        records,
    )
    conn.commit()


def insert_container_stats(
    conn: sqlite3.Connection,
    records: List[Dict[str, Any]],
) -> None:
    """Batch-insert container stats records."""
    if not records:
        return
    conn.executemany(
        """
        INSERT INTO container_stats
            (ts, container_id, name, image, rx_bytes, tx_bytes, rx_rate, tx_rate)
        VALUES
            (:ts, :container_id, :name, :image, :rx_bytes, :tx_bytes, :rx_rate, :tx_rate)
        """,
        records,
    )
    conn.commit()


def query_interface_history(
    conn: sqlite3.Connection,
    iface: str,
    hours: float = 1.0,
) -> List[Dict[str, Any]]:
    """Return time-series data for a single interface over the last N hours."""
    since = time.time() - hours * 3600
    rows = conn.execute(
        """
        SELECT ts, rx_rate, tx_rate
        FROM interface_stats
        WHERE iface = ? AND ts >= ?
        ORDER BY ts ASC
        """,
        (iface, since),
    ).fetchall()
    return [dict(r) for r in rows]


def query_container_history(
    conn: sqlite3.Connection,
    container_id: str,
    hours: float = 1.0,
) -> List[Dict[str, Any]]:
    """Return time-series data for a single container over the last N hours."""
    since = time.time() - hours * 3600
    rows = conn.execute(
        """
        SELECT ts, rx_rate, tx_rate
        FROM container_stats
        WHERE container_id = ? AND ts >= ?
        ORDER BY ts ASC
        """,
        (container_id, since),
    ).fetchall()
    return [dict(r) for r in rows]


def query_latest_interface_stats(
    conn: sqlite3.Connection,
) -> List[Dict[str, Any]]:
    """Return the most recent record per interface."""
    rows = conn.execute(
        """
        SELECT iface, ts, rx_bytes, tx_bytes, rx_rate, tx_rate
        FROM interface_stats
        WHERE ts = (
            SELECT MAX(ts) FROM interface_stats i2 WHERE i2.iface = interface_stats.iface
        )
        ORDER BY (rx_rate + tx_rate) DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def query_latest_container_stats(
    conn: sqlite3.Connection,
) -> List[Dict[str, Any]]:
    """Return the most recent record per container."""
    rows = conn.execute(
        """
        SELECT container_id, name, image, ts, rx_bytes, tx_bytes, rx_rate, tx_rate
        FROM container_stats
        WHERE ts = (
            SELECT MAX(ts) FROM container_stats c2
            WHERE c2.container_id = container_stats.container_id
        )
        ORDER BY (rx_rate + tx_rate) DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def purge_old_data(conn: sqlite3.Connection, hours: float = 168.0) -> None:
    """Delete records older than N hours to keep the DB size bounded."""
    cutoff = time.time() - hours * 3600
    conn.execute("DELETE FROM interface_stats WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM container_stats WHERE ts < ?", (cutoff,))
    conn.commit()
