"""
Docker container network stats collector.
Uses the Docker SDK to read per-container RX/TX byte counters and compute rates.
"""

import time
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Internal state: {container_id: (ts, rx_bytes, tx_bytes)}
_prev: Dict[str, tuple] = {}
_docker_client: Optional[Any] = None


def _get_client():
    """Lazily create a Docker client (falls back gracefully if Docker is unavailable)."""
    global _docker_client
    if _docker_client is not None:
        return _docker_client
    try:
        import docker
        _docker_client = docker.from_env()
        _docker_client.ping()
        logger.info("Docker client connected")
    except Exception as exc:
        logger.warning("Docker unavailable: %s — container monitoring disabled", exc)
        _docker_client = None
    return _docker_client


def _parse_docker_network_stats(stats: Dict) -> tuple[int, int]:
    """
    Extract total RX/TX bytes from a Docker stats payload.
    Sums across all network interfaces reported by the container.
    """
    net_data = stats.get("networks", {})
    rx_total = sum(v.get("rx_bytes", 0) for v in net_data.values())
    tx_total = sum(v.get("tx_bytes", 0) for v in net_data.values())
    return rx_total, tx_total


def read_container_rates() -> List[Dict[str, Any]]:
    """
    Return per-container network usage with instantaneous rates.

    Each item:
        {
          "container_id": "abc123...",
          "name": "jellyfin",
          "image": "jellyfin/jellyfin:latest",
          "status": "running",
          "rx_bytes": 1234567,
          "tx_bytes": 7654321,
          "rx_rate": 2048.0,   # bytes/s
          "tx_rate": 512.0,
          "ts": 1720000000.0,
        }
    """
    global _prev
    client = _get_client()
    if client is None:
        return []

    results: List[Dict[str, Any]] = []
    now = time.time()

    try:
        containers = client.containers.list()
    except Exception as exc:
        logger.error("Failed to list containers: %s", exc)
        return []

    for container in containers:
        cid = container.id[:12]
        name = container.name
        image = container.image.tags[0] if container.image.tags else container.image.short_id
        status = container.status

        try:
            raw_stats = container.stats(stream=False)
            rx, tx = _parse_docker_network_stats(raw_stats)
        except Exception as exc:
            logger.warning("Failed to get stats for %s: %s", name, exc)
            continue

        prev = _prev.get(cid)
        if prev:
            prev_ts, prev_rx, prev_tx = prev
            dt = now - prev_ts
            rx_rate = max(0.0, (rx - prev_rx) / dt) if dt > 0 else 0.0
            tx_rate = max(0.0, (tx - prev_tx) / dt) if dt > 0 else 0.0
        else:
            rx_rate = 0.0
            tx_rate = 0.0

        _prev[cid] = (now, rx, tx)

        results.append({
            "container_id": cid,
            "name": name,
            "image": image,
            "status": status,
            "rx_bytes": rx,
            "tx_bytes": tx,
            "rx_rate": rx_rate,
            "tx_rate": tx_rate,
            "ts": now,
        })

    # Sort by total rate descending
    results.sort(key=lambda x: x["rx_rate"] + x["tx_rate"], reverse=True)
    return results


def is_docker_available() -> bool:
    """Return True if Docker is reachable."""
    return _get_client() is not None
