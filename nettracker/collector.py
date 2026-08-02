"""
Background data collection engine for NetTracker.
Runs as an asyncio task; reads /proc/net/dev and Docker stats concurrently,
stores delta rates in SQLite, and broadcasts updates to WebSocket clients.
"""

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from nettracker import db
from nettracker import proc_stats
from nettracker import docker_stats

logger = logging.getLogger(__name__)

INTERVAL: float = float(os.environ.get("NETTRACKER_INTERVAL", "2"))
HISTORY_HOURS: float = float(os.environ.get("NETTRACKER_HISTORY_HOURS", "168"))  # 7 days
PURGE_EVERY: int = 3600  # seconds between DB purges

# Shared state — updated by the collector, read by API handlers
_latest: Dict[str, Any] = {
    "interfaces": [],
    "containers": [],
    "ts": 0.0,
    "docker_available": False,
}

# WebSocket broadcast callbacks registered by main.py
_broadcast_callbacks: List[Any] = []

# Thread pool for blocking Docker API calls
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="docker_stats")


def get_latest() -> Dict[str, Any]:
    """Return the most recent collected snapshot (non-blocking)."""
    return _latest.copy()


def register_broadcast(callback) -> None:
    """Register an async callable that will be called with the latest snapshot."""
    _broadcast_callbacks.append(callback)


def unregister_broadcast(callback) -> None:
    _broadcast_callbacks.discard(callback) if hasattr(_broadcast_callbacks, "discard") else None
    try:
        _broadcast_callbacks.remove(callback)
    except ValueError:
        pass


async def _fetch_docker_stats_async(loop: asyncio.AbstractEventLoop) -> List[Dict]:
    """Run Docker stats collection in a thread pool (blocking calls)."""
    return await loop.run_in_executor(_executor, docker_stats.read_container_rates)


async def _broadcast(snapshot: Dict[str, Any]) -> None:
    """Call all registered broadcast callbacks with the latest snapshot."""
    dead = []
    for cb in list(_broadcast_callbacks):
        try:
            await cb(snapshot)
        except Exception as exc:
            logger.debug("Broadcast callback error: %s", exc)
            dead.append(cb)
    for cb in dead:
        try:
            _broadcast_callbacks.remove(cb)
        except ValueError:
            pass


async def collection_loop(db_conn) -> None:
    """
    Main collection loop — runs forever as an asyncio background task.
    Every INTERVAL seconds:
      1. Read /proc/net/dev for interface rates
      2. Read Docker container stats concurrently
      3. Persist to SQLite
      4. Broadcast to WebSocket clients
    Every PURGE_EVERY seconds:
      5. Delete records older than HISTORY_HOURS
    """
    global _latest
    loop = asyncio.get_running_loop()
    last_purge = time.time()

    logger.info(
        "Collection loop started (interval=%.1fs, history=%.0fh)",
        INTERVAL,
        HISTORY_HOURS,
    )

    # Warm up: first read establishes baseline counters (rates will be 0)
    proc_stats.read_interface_rates()
    if docker_stats.is_docker_available():
        await _fetch_docker_stats_async(loop)
    await asyncio.sleep(INTERVAL)

    while True:
        tick_start = time.time()

        # --- Interface stats (non-blocking, pure Python) ---
        iface_data = proc_stats.read_interface_rates()
        iface_list = sorted(
            iface_data.values(),
            key=lambda x: x["rx_rate"] + x["tx_rate"],
            reverse=True,
        )

        # --- Docker container stats (blocking, run in thread pool) ---
        container_list: List[Dict] = []
        if docker_stats.is_docker_available():
            try:
                container_list = await asyncio.wait_for(
                    _fetch_docker_stats_async(loop),
                    timeout=20.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Docker stats collection timed out")
            except Exception as exc:
                logger.error("Docker stats error: %s", exc)

        # --- Update shared state ---
        snapshot = {
            "interfaces": iface_list,
            "containers": container_list,
            "ts": tick_start,
            "docker_available": docker_stats.is_docker_available(),
        }
        _latest = snapshot

        # --- Persist to SQLite ---
        try:
            iface_records = [
                {
                    "ts": r["ts"],
                    "iface": r["iface"],
                    "rx_bytes": r["rx_bytes"],
                    "tx_bytes": r["tx_bytes"],
                    "rx_rate": r["rx_rate"],
                    "tx_rate": r["tx_rate"],
                }
                for r in iface_list
            ]
            db.insert_interface_stats(db_conn, iface_records)

            container_records = [
                {
                    "ts": c["ts"],
                    "container_id": c["container_id"],
                    "name": c["name"],
                    "image": c["image"],
                    "rx_bytes": c["rx_bytes"],
                    "tx_bytes": c["tx_bytes"],
                    "rx_rate": c["rx_rate"],
                    "tx_rate": c["tx_rate"],
                }
                for c in container_list
            ]
            db.insert_container_stats(db_conn, container_records)
        except Exception as exc:
            logger.error("DB insert error: %s", exc)

        # --- Periodic purge ---
        now = time.time()
        if now - last_purge >= PURGE_EVERY:
            try:
                db.purge_old_data(db_conn, hours=HISTORY_HOURS)
                logger.info("Purged data older than %.0f hours", HISTORY_HOURS)
            except Exception as exc:
                logger.error("DB purge error: %s", exc)
            last_purge = now

        # --- Broadcast to WebSocket clients ---
        await _broadcast(snapshot)

        # --- Sleep for remainder of interval ---
        elapsed = time.time() - tick_start
        sleep_time = max(0.0, INTERVAL - elapsed)
        await asyncio.sleep(sleep_time)
