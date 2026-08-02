"""
NetTracker CLI — rich terminal interface for network monitoring.
Usage:
    python -m nettracker.cli watch        # live updating table
    python -m nettracker.cli top          # one-shot top consumers
    python -m nettracker.cli interfaces   # interface overview
    python -m nettracker.cli serve        # start the web server
"""

import os
import sys
import time
import threading
import click
import logging

logging.basicConfig(level=logging.WARNING)


def _format_bytes(n: float) -> str:
    """Format bytes/s into human-readable string."""
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if abs(n) < 1024.0:
            return f"{n:6.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB/s"


def _format_total(n: int) -> str:
    """Format total bytes into human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


@click.group()
def cli():
    """🌐 NetTracker — Linux Network Traffic Monitor"""
    pass


@cli.command()
@click.option("--interval", "-i", default=2.0, help="Refresh interval in seconds", show_default=True)
@click.option("--limit", "-n", default=20, help="Max rows to show", show_default=True)
def watch(interval: float, limit: int):
    """Live updating terminal table (like htop for network)."""
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.panel import Panel
    from rich.columns import Columns
    from rich.text import Text
    from nettracker import proc_stats, docker_stats

    console = Console()

    def build_display():
        # --- Interface table ---
        iface_table = Table(
            title="[bold cyan]Network Interfaces[/]",
            show_header=True,
            header_style="bold magenta",
            border_style="dim",
            expand=True,
        )
        iface_table.add_column("Interface", style="bold white", min_width=18)
        iface_table.add_column("↓ RX Rate", justify="right", style="green")
        iface_table.add_column("↑ TX Rate", justify="right", style="red")
        iface_table.add_column("Total RX", justify="right", style="dim green")
        iface_table.add_column("Total TX", justify="right", style="dim red")

        iface_data = proc_stats.read_interface_rates()
        sorted_ifaces = sorted(
            iface_data.values(),
            key=lambda x: x["rx_rate"] + x["tx_rate"],
            reverse=True,
        )
        for r in sorted_ifaces[:limit]:
            iface_table.add_row(
                r["iface"],
                _format_bytes(r["rx_rate"]),
                _format_bytes(r["tx_rate"]),
                _format_total(r["rx_bytes"]),
                _format_total(r["tx_bytes"]),
            )

        # --- Container table ---
        container_table = Table(
            title="[bold cyan]Docker Containers[/]",
            show_header=True,
            header_style="bold magenta",
            border_style="dim",
            expand=True,
        )
        container_table.add_column("Container", style="bold white", min_width=22)
        container_table.add_column("Image", style="dim", min_width=18)
        container_table.add_column("↓ RX Rate", justify="right", style="green")
        container_table.add_column("↑ TX Rate", justify="right", style="red")
        container_table.add_column("Total RX", justify="right", style="dim green")
        container_table.add_column("Total TX", justify="right", style="dim red")

        containers = docker_stats.read_container_rates()
        if not containers and not docker_stats.is_docker_available():
            container_table.add_row("[dim]Docker not available[/]", "", "", "", "", "")
        else:
            for c in containers[:limit]:
                container_table.add_row(
                    c["name"],
                    c["image"][:30],
                    _format_bytes(c["rx_rate"]),
                    _format_bytes(c["tx_rate"]),
                    _format_total(c["rx_bytes"]),
                    _format_total(c["tx_bytes"]),
                )

        ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
        header = Panel(
            Text(f"🌐 NetTracker  —  {ts_str}  —  Press Ctrl+C to exit", justify="center"),
            style="bold blue",
        )
        return Columns([header]), iface_table, container_table

    # Warm up first read to establish baselines
    proc_stats.read_interface_rates()
    if docker_stats.is_docker_available():
        docker_stats.read_container_rates()
    time.sleep(interval)

    with Live(console=console, refresh_per_second=1.0 / interval, screen=False) as live:
        while True:
            try:
                from rich.console import Group
                header_cols, it, ct = build_display()
                live.update(Group(header_cols[0] if header_cols else "", it, ct))
                time.sleep(interval)
            except KeyboardInterrupt:
                break


@cli.command()
@click.option("--limit", "-n", default=10, help="Number of top consumers to show", show_default=True)
def top(limit: int):
    """Show top N network consumers (one-shot)."""
    from rich.console import Console
    from rich.table import Table
    from nettracker import proc_stats, docker_stats

    console = Console()

    # Warm up
    proc_stats.read_interface_rates()
    if docker_stats.is_docker_available():
        docker_stats.read_container_rates()
    time.sleep(2)

    console.print("\n[bold cyan]Top Network Consumers[/]\n")

    # Interfaces
    iface_table = Table(show_header=True, header_style="bold magenta", border_style="dim")
    iface_table.add_column("Interface", style="bold white")
    iface_table.add_column("↓ RX", justify="right", style="green")
    iface_table.add_column("↑ TX", justify="right", style="red")

    iface_data = proc_stats.read_interface_rates()
    for r in sorted(iface_data.values(), key=lambda x: x["rx_rate"] + x["tx_rate"], reverse=True)[:limit]:
        iface_table.add_row(r["iface"], _format_bytes(r["rx_rate"]), _format_bytes(r["tx_rate"]))
    console.print(iface_table)

    # Containers
    if docker_stats.is_docker_available():
        console.print()
        ct = Table(show_header=True, header_style="bold magenta", border_style="dim")
        ct.add_column("Container", style="bold white")
        ct.add_column("Image", style="dim")
        ct.add_column("↓ RX", justify="right", style="green")
        ct.add_column("↑ TX", justify="right", style="red")
        for c in docker_stats.read_container_rates()[:limit]:
            ct.add_row(c["name"], c["image"][:25], _format_bytes(c["rx_rate"]), _format_bytes(c["tx_rate"]))
        console.print(ct)
    console.print()


@cli.command()
def interfaces():
    """Show all network interfaces with current stats."""
    from rich.console import Console
    from rich.table import Table
    from nettracker import proc_stats

    console = Console()
    proc_stats.read_interface_rates()
    time.sleep(1.5)
    data = proc_stats.read_interface_rates()

    table = Table(title="[bold cyan]Network Interfaces[/]", show_header=True,
                  header_style="bold magenta", border_style="dim")
    table.add_column("Interface", style="bold white")
    table.add_column("↓ RX Rate", justify="right", style="green")
    table.add_column("↑ TX Rate", justify="right", style="red")
    table.add_column("Total RX", justify="right", style="dim green")
    table.add_column("Total TX", justify="right", style="dim red")

    for r in sorted(data.values(), key=lambda x: x["rx_bytes"] + x["tx_bytes"], reverse=True):
        table.add_row(
            r["iface"],
            _format_bytes(r["rx_rate"]),
            _format_bytes(r["tx_rate"]),
            _format_total(r["rx_bytes"]),
            _format_total(r["tx_bytes"]),
        )
    console.print(table)


@cli.command()
@click.option("--host", default=None, help="Host to bind (default: NETTRACKER_HOST env or 0.0.0.0)")
@click.option("--port", default=None, type=int, help="Port (default: NETTRACKER_PORT env or 7654)")
def serve(host: str, port: int):
    """Start the NetTracker web server."""
    import uvicorn
    h = host or os.environ.get("NETTRACKER_HOST", "0.0.0.0")
    p = port or int(os.environ.get("NETTRACKER_PORT", "7654"))
    click.echo(f"🌐 NetTracker starting on http://{h}:{p}")
    uvicorn.run("nettracker.main:app", host=h, port=p, reload=False, log_level="info")


def main():
    cli()


if __name__ == "__main__":
    main()
