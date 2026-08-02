# 🌐 NetTracker — Linux Network Traffic Monitor

> Real-time network bandwidth monitoring for Linux systems — per application, Docker container, and network service.

![NetTracker Dashboard](https://img.shields.io/badge/platform-Linux%20%7C%20aarch64-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)
![License](https://img.shields.io/badge/license-MIT-orange)

---

## ✨ Features

- 📊 **Real-time dashboard** — live bandwidth usage per Docker container and network interface
- 🐳 **Docker-aware** — automatically discovers and tracks all running containers
- 📈 **Historical graphs** — 24-hour traffic history stored in SQLite
- ⚡ **WebSocket push** — instant updates every 2 seconds, no polling
- 🖥️ **CLI mode** — rich terminal table with live updating
- 🔧 **Systemd service** — runs as a background service on boot
- 🌙 **Dark-mode UI** — beautiful glassmorphism dashboard

---

## 📋 Requirements

- Linux (Ubuntu 22.04+ recommended)
- Python 3.10+
- Docker (optional, for container monitoring)
- User must be in the `docker` group for container monitoring

---

## 🚀 Quick Install

```bash
git clone https://github.com/YOUR_USERNAME/traffic_stats.git
cd traffic_stats
./install.sh
```

This will:
1. Install Python dependencies
2. Create the `nettracker` CLI command
3. Install and enable the systemd service

---

## 🖥️ Web Dashboard

Access the dashboard at:

```
http://YOUR_IP:7654
```

The dashboard shows:
- **Top consumers** — containers/processes ranked by current bandwidth
- **Interface overview** — total RX/TX per network interface
- **Live charts** — animated real-time graphs per container
- **Historical view** — 24-hour traffic area chart

---

## 💻 CLI Usage

```bash
# Live updating terminal table (like htop but for network)
nettracker watch

# Show current top consumers
nettracker top

# Show all interfaces
nettracker interfaces

# Start the web server manually
nettracker serve --host 0.0.0.0 --port 7654
```

---

## ⚙️ Configuration

Configure via environment variables or `.env` file:

```env
NETTRACKER_HOST=0.0.0.0
NETTRACKER_PORT=7654
NETTRACKER_DB_PATH=/var/lib/nettracker/nettracker.db
NETTRACKER_INTERVAL=2
NETTRACKER_HISTORY_HOURS=24
```

---

## 🔧 Systemd Service

```bash
# Start/stop/restart
sudo systemctl start nettracker
sudo systemctl stop nettracker
sudo systemctl restart nettracker

# Check status
sudo systemctl status nettracker

# View logs
journalctl -u nettracker -f
```

---

## 🐳 Docker Deployment

Run NetTracker itself inside a container:

```bash
docker compose up -d
```

> **Note**: Requires `--net=host` and Docker socket mount for full monitoring capabilities.

---

## 📁 Project Structure

```
traffic_stats/
├── nettracker/
│   ├── main.py          # FastAPI app + WebSocket server
│   ├── collector.py     # Background data collection engine
│   ├── docker_stats.py  # Docker container monitoring
│   ├── proc_stats.py    # /proc/net/dev reader
│   ├── db.py            # SQLite storage layer
│   └── cli.py           # Click CLI entrypoint
├── static/
│   ├── index.html       # Dashboard UI
│   ├── style.css        # Dark glassmorphism design
│   └── app.js           # Real-time charts + WebSocket
├── systemd/
│   └── nettracker.service
├── install.sh
├── docker-compose.yml
└── requirements.txt
```

---

## 🛡️ Security Notes

<!-- TODO(security): Add HTTP Basic Auth or Bearer token authentication -->
<!-- TODO(security): Consider OAuth2 for multi-user environments -->
<!-- TODO(security): Consider MFA for admin access -->

- The API currently has **no authentication** — suitable for trusted LAN environments
- Docker socket access requires the `docker` group membership
- No credentials are stored in code; configure via environment variables

---

## 📜 License

MIT © 2026 NetTracker Contributors
