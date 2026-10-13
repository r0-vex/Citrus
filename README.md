![Python](https://img.shields.io/badge/Python-3.14-blue)

![Version](https://img.shields.io/badge/version-v1.3.0-success)

![License](https://img.shields.io/badge/license-MIT-green)

![Status](https://img.shields.io/badge/status-Active-success)

# 🍋 Citrus

> A lightweight personal homelab and server control platform built on a repurposed Samsung Galaxy J7 Max using Termux.

---

## 📖 About

**Citrus** is a personal infrastructure platform built to learn and experiment with:

- Linux
- Server administration
- Python development
- Process management
- Automation
- Networking
- Self-hosting
- Cross-platform development

The project transforms an old Android phone into a practical development server capable of running and managing real services.

Citrus is designed as a modular platform rather than a collection of standalone scripts.

---

## 🎯 Current Capabilities

### 🍋 Citrus Core

The Citrus Core provides the foundation for the entire platform.

Current architecture includes:

- Modular application structure
- Centralized configuration
- Structured logging
- Event tracking
- Runtime state management
- Custom exception handling
- Modular system information collectors
- Page-based user interface
- Central application lifecycle management

---

### ⚙️ Service Manager

Citrus includes a process-based Service Manager for running, controlling, and diagnosing background services independently from the Citrus application.

Current capabilities:

- Service discovery & JSON registry validation
- Process identity verification & PID-reuse protection (`ProcessIdentity`)
- OS-level process fingerprinting (POSIX `/proc/<pid>/stat` clock ticks & boot ID; Windows 64-bit FILETIME creation tokens)
- Safe process termination (process group isolation on POSIX; retained pointer-sized handle termination on Windows)
- Defensive runtime-state persistence (atomic writes, legacy bare-PID clearing, surviving restarts)
- Application-level health monitoring (`heartbeat_file`, loopback HTTP, loopback TCP)
- Loopback-only network probes (`127.0.0.1`, `localhost`, `[::1]`) with safe redirect filtering
- Short probe caching (2.0s TTL) with on-demand manual refresh
- Memory-bounded service-log tailing (`get_log_tail`)
- Terminal UI health badges (`RUNNING [HEALTHY]`) and diagnostic detail views
- Per-service logging under `logs/services/`
- Lifecycle tracking (restart counts, started timestamps, last-exit codes, error details)

*Note on current design:* Health checks report diagnostic health; automatic restart supervision remains reserved and deferred.

Service architecture:

```text
Service Registry & Config
       │
       ▼
Service Manager ──────► Health Evaluator (Heartbeat / HTTP / TCP)
       │
       ▼
Process Runner (Identity Verification & Termination Safety)
       │
       ├── Start
       ├── Stop
       ├── Restart
       └── Status
```

The Service Manager has been tested on both **Windows** and the actual **Android / Termux server**.

---

## 📊 System Dashboard

Citrus provides a terminal-based system dashboard displaying information such as:

- Hostname
- Current user
- Operating system
- Kernel version
- Python version
- Git version
- Working directory
- SSH status
- Internet status
- Local IP address
- CPU information
- CPU core count
- RAM usage
- Storage usage
- Battery percentage
- Citrus version
- Runtime information
- System uptime

System information collection is separated from UI rendering through dedicated collectors.

---

## 📝 Logging & Events

Citrus uses centralized structured logging.

Current log structure:

```text
logs/
├── system.log
├── events.log
├── errors.log
├── git.log
├── debug.log
└── services/
```

The logging system records:

- Application sessions
- Application start/stop events
- Page navigation
- Service activity
- Git activity
- Errors
- Debug information

Events include:

```text
APP_STARTED
APP_STOPPED
PAGE_OPENED
PAGE_CLOSED
```

Service logs are isolated under the `logs/services/` directory.

---

## 🖥️ Platform Support

Citrus is currently developed and tested across:

| Environment | Status |
|-------------|--------|
| Windows | ✅ Tested |
| Android 8.1 | ✅ Tested |
| Termux | ✅ Tested |
| Samsung Galaxy J7 Max | ✅ Tested |

The Samsung Galaxy J7 Max is the primary Citrus server.

---

# 🛠️ Hardware

| Component | Specification |
|-----------|---------------|
| Device | Samsung Galaxy J7 Max |
| RAM | 4 GB |
| Storage | 24 GB Internal + 32 GB SD Card |
| CPU | MediaTek MT6757 |
| OS | Android 8.1 |
| Terminal | Termux |
| Shell | Zsh |

---

# 📁 Project Structure

```text
Citrus/
├── bots/
├── services/
├── web/
├── media/
├── logs/
├── configs/
├── core/
│   ├── service_manager/
│   ├── system_info/
│   ├── pages/
│   ├── logging/
│   ├── app.py
│   ├── menu.py
│   └── citrus.py
├── backups/
├── scripts/
└── temp/
```

---

# 🚀 Running Citrus

Clone the repository:

```bash
git clone git@github.com:r0-vex/Citrus.git
cd Citrus
```

Run Citrus:

```bash
python -m core.citrus
```

Citrus provides a terminal-based control center for accessing its modules.

---

# 🔄 Development Workflow

Citrus follows a Git-based development and deployment workflow.

```text
        Windows Laptop
              │
              ▼
          Develop
              │
              ▼
        Git Commit
              │
              ▼
        Git Push
              │
              ▼
           GitHub
              │
              ▼
         Git Pull
              │
              ▼
       Citrus Server
        (Android)
```

The laptop is the primary development environment.

GitHub acts as the source of truth.

The Samsung Galaxy J7 Max acts as the deployment and runtime server.

---

# 🧪 Testing

Citrus is tested in both the development environment and the actual target server environment.

The codebase includes an automated unit test suite with 146 tests running in sub-second time, using isolated mocks and temporary directories with zero real process-signalling or live external network side effects:

- Core compilation (`python -m compileall -q core tests`)
- Application startup and session lifecycle
- Menu navigation and dashboard rendering
- Logging and event tracking
- Service discovery and strict JSON definition validation
- Process identity verification (POSIX `/proc/<pid>/stat` clock ticks & boot ID; Windows FILETIME)
- Safe process termination (POSIX process groups; Windows 64-bit retained handles)
- Signal refusal on unverified, mismatched, or inconclusive identities
- Corrupt and legacy persisted state recovery
- Application health checks (heartbeat file mtime freshness, loopback HTTP, loopback TCP)
- HTTP/TCP URL and target parsing resilience against malformed ports
- Safe redirect handling blocking non-loopback destinations
- Bounded memory log tailing
- Cross-platform compatibility (Windows and Android/Termux)

---

# 📚 Development Log

The development process, architecture decisions, debugging sessions, and daily progress are documented separately in:

```text
DEVLOG.md
```

The development log records the evolution of Citrus from the initial Android server setup through the current modular architecture.

---

# 🗺️ Roadmap

### Completed

- [x] Android server setup
- [x] Termux environment
- [x] SSH remote administration
- [x] Git-based deployment
- [x] Citrus Core v1.0
- [x] Centralized logging
- [x] Event system
- [x] System information collectors
- [x] Modular dashboard
- [x] Service Manager
- [x] Cross-platform process management
- [x] Windows testing
- [x] Termux testing
- [x] Android server validation
- [x] Service-state persistence hardening
- [x] Lifecycle metadata and restart tracking
- [x] Service registry validation
- [x] Process identity verification & PID reuse protection (Day 6)
- [x] Service Manager reliability hardening & 146 unit tests (Day 6)
- [x] Service health monitoring & evaluators (Day 7)
- [x] Loopback HTTP/TCP & heartbeat file health checks (Day 7)
- [x] Bounded service log viewer (Day 7)

### Planned

- [ ] Automatic service restart supervision
- [ ] Bot Manager
- [ ] Python bot deployment
- [ ] Automation engine
- [ ] Flask API
- [ ] Web Dashboard
- [ ] Website hosting
- [ ] Personal music server
- [ ] File services
- [ ] Monitoring system
- [ ] Backup automation

---

# 🌱 Vision

Citrus is more than an old Android phone.

It is a personal homelab platform built to learn, experiment, and host real services while gaining practical experience with:

- Linux
- Networking
- Python
- Server administration
- Process management
- Automation
- Self-hosting
- Software architecture

The long-term goal is to transform Citrus into a modular personal infrastructure platform capable of managing multiple services from a single control center.

Every new capability will be developed, tested, documented, and deployed incrementally.

---

## 📌 Current Release

**Citrus v1.3.0**

Current milestone:

> **Core v1.0 + Hardened Service Manager & Health Monitoring**

---

**Project Started:** 24 July 2026 🍋
