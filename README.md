![Python](https://img.shields.io/badge/Python-3.14-blue)

![Version](https://img.shields.io/badge/version-v1.1.0-success)

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

Citrus includes a process-based Service Manager for running and controlling background services independently from the Citrus application.

Current capabilities:

- Service discovery
- Service registry
- Service definitions
- Process spawning
- PID tracking
- Running-state detection
- Start / Stop / Restart
- Persistent service state
- Per-service logging
- Service lifecycle management
- Process cleanup
- Cross-platform process handling

Service architecture:

```text
Service Registry
       │
       ▼
Service Manager
       │
       ▼
Process Runner
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

Testing currently covers:

- Core compilation
- Application startup
- Menu navigation
- Dashboard
- Logging
- Event tracking
- Service discovery
- Service lifecycle
- Process management
- PID tracking
- Service persistence
- Cross-platform process handling
- Termux compatibility

The Service Manager has been specifically validated against the real Android / Termux environment.

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

### Planned

- [ ] Service health monitoring
- [ ] Expanded service configuration
- [ ] Advanced service metadata
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

**Citrus v1.1.0**

Current milestone:

> **Core v1.0 + Service Manager**

---

**Project Started:** 24 July 2026 🍋
