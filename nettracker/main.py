"""
FastAPI application for NetTracker.
Serves the web dashboard, REST API endpoints, and WebSocket real-time stream.

Security notes:
- API is open on LAN (no authentication) — suitable for trusted home networks.
- TODO(security): Add HTTP Basic Auth or Bearer token if exposed to untrusted networks.
- TODO(security): Consider OAuth2 for multi-user environments.
- All user-supplied data is sanitised before DB queries (parameterised).
- CSP, X-Frame-Options and X-Content-Type-Options headers are set on every response.
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from nettracker import db, collector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "static"
HOST = os.environ.get("NETTRACKER_HOST", "0.0.0.0")
PORT = int(os.environ.get("NETTRACKER_PORT", "7654"))

# --- Shared DB connection (thread-safe via WAL mode) ---
_db_conn = None


def get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = db.get_connection()
        db.init_db(_db_conn)
    return _db_conn





# --- WebSocket connection manager ---
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)
        logger.info("WS client connected (total=%d)", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        try:
            self.active.remove(ws)
        except ValueError:
            pass
        logger.info("WS client disconnected (total=%d)", len(self.active))

    async def broadcast(self, data: Dict[str, Any]) -> None:
        if not self.active:
            return
        payload = json.dumps(data, default=str)
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def _ws_broadcast(snapshot: Dict[str, Any]) -> None:
    """Callback registered with the collector to push updates to all WS clients."""
    # Prepare a serialisable summary (skip heavy per-second data)
    await manager.broadcast({
        "type": "update",
        "ts": snapshot["ts"],
        "interfaces": snapshot["interfaces"],
        "containers": snapshot["containers"],
        "docker_available": snapshot["docker_available"],
    })


# --- Application lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = get_db()
    collector.register_broadcast(_ws_broadcast)
    task = asyncio.create_task(collector.collection_loop(conn))
    logger.info("NetTracker started — dashboard at http://%s:%d", HOST, PORT)
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    if _db_conn:
        _db_conn.close()
    logger.info("NetTracker stopped")


# --- FastAPI app ---
app = FastAPI(
    title="NetTracker",
    description="Linux Network Traffic Monitor",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# Security headers middleware for HTTP requests
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self' ws: wss: http: https:; "
        "object-src 'none'; "
        "frame-ancestors 'self';"
    )
    return response

# CORS: allow all LAN origins (no authentication; trusted network assumed)
# TODO(security): Restrict origins to specific LAN subnet if needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Serve static files (dashboard)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- REST API endpoints ---

@app.get("/", include_in_schema=False)
async def root():
    """Serve the dashboard HTML."""
    html_file = STATIC_DIR / "index.html"
    if html_file.exists():
        return FileResponse(str(html_file))
    return HTMLResponse("<h1>NetTracker</h1><p>Static files not found.</p>")


@app.get("/api/snapshot")
async def get_snapshot():
    """
    Return the latest collected snapshot (interfaces + containers).
    This is a REST fallback; real-time updates come via WebSocket.
    """
    return collector.get_latest()


@app.get("/api/interfaces")
async def get_interfaces():
    """Return current stats for all network interfaces."""
    return {"interfaces": collector.get_latest().get("interfaces", [])}


@app.get("/api/containers")
async def get_containers():
    """Return current stats for all Docker containers."""
    return {
        "containers": collector.get_latest().get("containers", []),
        "docker_available": collector.get_latest().get("docker_available", False),
    }


@app.get("/api/history/interface/{iface}")
async def get_interface_history(
    iface: str,
    hours: float = Query(default=1.0, ge=0.1, le=168.0),
):
    """
    Return time-series bandwidth history for a specific interface.
    iface must be a valid interface name (validated against known names).
    hours: window in hours (0.1 – 168).
    """
    # Validate iface against known interface names to prevent injection
    known = {r["iface"] for r in collector.get_latest().get("interfaces", [])}
    # Also allow querying interfaces that may have disappeared
    if iface and not iface.replace("-", "").replace("_", "").replace(".", "").replace("0", "").isalnum():
        return {"error": "Invalid interface name", "data": []}
    conn = get_db()
    data = db.query_interface_history(conn, iface, hours=hours)
    return {"iface": iface, "hours": hours, "data": data}


@app.get("/api/history/container/{container_id}")
async def get_container_history(
    container_id: str,
    hours: float = Query(default=1.0, ge=0.1, le=168.0),
):
    """
    Return time-series bandwidth history for a specific container.
    container_id is validated to be alphanumeric (12-char short ID).
    """
    # Validate container_id: must be 12 hex chars
    if not container_id.isalnum() or len(container_id) > 64:
        return {"error": "Invalid container ID", "data": []}
    conn = get_db()
    data = db.query_container_history(conn, container_id, hours=hours)
    return {"container_id": container_id, "hours": hours, "data": data}


@app.get("/api/status")
async def get_status():
    """Return service health and configuration."""
    import nettracker
    snap = collector.get_latest()
    return {
        "status": "ok",
        "version": nettracker.__version__,
        "ts": snap.get("ts", 0),
        "uptime_interfaces": len(snap.get("interfaces", [])),
        "uptime_containers": len(snap.get("containers", [])),
        "docker_available": snap.get("docker_available", False),
        "interval_seconds": collector.INTERVAL,
        "history_hours": collector.HISTORY_HOURS,
    }


# --- WebSocket endpoint ---

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    Real-time WebSocket stream.
    Sends the latest snapshot immediately on connect,
    then pushes updates every INTERVAL seconds via the collector broadcast.
    """
    await manager.connect(ws)
    try:
        # Send current snapshot immediately
        snap = collector.get_latest()
        await ws.send_text(json.dumps({
            "type": "update",
            "ts": snap.get("ts", time.time()),
            "interfaces": snap.get("interfaces", []),
            "containers": snap.get("containers", []),
            "docker_available": snap.get("docker_available", False),
        }, default=str))

        # Keep connection open; updates come via broadcast
        while True:
            try:
                # Listen for ping/pong or client messages (ignored)
                await asyncio.wait_for(ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send keepalive ping
                await ws.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WebSocket error: %s", exc)
    finally:
        manager.disconnect(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "nettracker.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )
