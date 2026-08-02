"""
Read network stats from /proc/net/dev.
Provides byte counters and computed rates for each interface.
"""

import time
import logging
from pathlib import Path
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# Interfaces to skip (virtual loopback, zero-traffic placeholders)
_SKIP_PREFIXES = ("lo",)

# /proc/net/dev column indices (0-based after splitting)
# Format: iface: rx_bytes packets errs drop fifo frame compressed multicast
#                tx_bytes packets errs drop fifo colls carrier compressed
_RX_BYTES_IDX = 0
_TX_BYTES_IDX = 8

# Internal state: {iface: (ts, rx_bytes, tx_bytes)}
_prev: Dict[str, Tuple[float, int, int]] = {}


def _parse_proc_net_dev() -> Dict[str, Tuple[int, int]]:
    """Parse /proc/net/dev and return {iface: (rx_bytes, tx_bytes)}."""
    result: Dict[str, Tuple[int, int]] = {}
    try:
        lines = Path("/proc/net/dev").read_text().splitlines()
    except OSError as exc:
        logger.error("Cannot read /proc/net/dev: %s", exc)
        return result

    for line in lines[2:]:  # skip header rows
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        if any(iface.startswith(p) for p in _SKIP_PREFIXES):
            continue
        cols = rest.split()
        if len(cols) < 16:
            continue
        try:
            rx = int(cols[_RX_BYTES_IDX])
            tx = int(cols[_TX_BYTES_IDX])
        except ValueError:
            continue
        result[iface] = (rx, tx)
    return result


def read_interface_rates() -> Dict[str, Dict]:
    """
    Read /proc/net/dev and compute instantaneous RX/TX rates (bytes/s).
    Attaches human-readable owner labels (e.g., Primary LAN, Docker Net: xxx).
    """
    global _prev
    now = time.time()
    raw = _parse_proc_net_dev()
    result: Dict[str, Dict] = {}

    # Fetch Docker network labels if available
    docker_labels: Dict[str, str] = {}
    try:
        from nettracker import docker_stats
        docker_labels = docker_stats.get_network_labels()
    except Exception:
        pass

    for iface, (rx, tx) in raw.items():
        prev = _prev.get(iface)
        if prev:
            prev_ts, prev_rx, prev_tx = prev
            dt = now - prev_ts
            rx_rate = max(0.0, (rx - prev_rx) / dt) if dt > 0 else 0.0
            tx_rate = max(0.0, (tx - prev_tx) / dt) if dt > 0 else 0.0
        else:
            rx_rate = 0.0
            tx_rate = 0.0

        # Determine human readable owner label
        label = docker_labels.get(iface, "")
        if not label:
            if iface in ("eth0", "eth1", "enp0s3", "end0") or iface.startswith(("eth", "enp", "end", "wlan")):
                label = "Primary LAN"
            elif iface == "docker0":
                label = "Docker Default Bridge"
            elif iface.startswith("br-"):
                label = "Docker Network Bridge"
            elif iface.startswith("veth"):
                label = "Docker Container Interface"
            elif iface.startswith(("tailscale", "tun", "wg")):
                label = "Tailscale / VPN"
            elif iface == "lo":
                label = "Loopback"
            else:
                label = "Network Interface"

        _prev[iface] = (now, rx, tx)
        result[iface] = {
            "iface": iface,
            "label": label,
            "rx_bytes": rx,
            "tx_bytes": tx,
            "rx_rate": rx_rate,
            "tx_rate": tx_rate,
            "ts": now,
        }

    return result


def get_interface_names() -> list:
    """Return all currently visible network interface names."""
    return list(_parse_proc_net_dev().keys())
