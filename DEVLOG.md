# Development Log

---

## 📅 Day 1 — Foundation & Environment Setup

### Device Preparation

- ✅ Factory Reset
- ✅ Updated to latest Android firmware
- ✅ Disabled unnecessary Samsung/Google applications
- ✅ Enabled Developer Options
- ✅ Enabled USB Debugging
- ✅ Disabled system animations
- ✅ Optimized device for server usage
- ✅ Renamed device to **Citrus-01**

---

### Linux Environment

Installed:

- ✅ Termux
- ✅ Python
- ✅ Git
- ✅ OpenSSH
- ✅ tmux
- ✅ Fastfetch
- ✅ Zsh

Configured:

- ✅ Storage access
- ✅ SSH key pair
- ✅ Git global configuration
- ✅ Server directory structure

---

### Remote Administration

Successfully configured:

- ✅ SSH Server
- ✅ Password authentication
- ✅ Remote login from Windows laptop
- ✅ Terminal management through SSH

---

### Shell Customization

Customized Zsh with:

- 🍋 Custom Citrus prompt
- ⚡ Useful aliases
- 📜 Persistent command history
- 🎨 Colored prompt
- 🚀 Login banner
- 💻 Fastfetch system overview
- 🔧 Improved shell experience

---

## Current Status

```text
Infrastructure      ✅ Complete
Remote Access       ✅ Complete
Development Shell   ✅ Complete

Ready for:
⬜ Dashboard
⬜ Service Manager
⬜ Python Bots
⬜ Music Server
⬜ Flask API
⬜ Website Hosting
```

---

## 📅 Day 2 — Git Workflow & Project Architecture

### Project Structure

Designed the first modular architecture for Citrus.

Created the initial core application structure:

```text
core/
├── citrus.py
├── menu.py
├── dashboard.py
├── services.py
├── git_tools.py
├── system.py
├── utils.py
├── config.py
└── __init__.py
```

Planned Citrus as a scalable platform rather than a single Python script.

---

### Development Workflow

Explored multiple development approaches:

- VS Code Remote SSH
- SFTP Auto Upload
- Git-based Deployment

After testing each option, decided to adopt a professional Git workflow.

Final workflow:

```text
Laptop (VS Code)
        │
        ▼
Edit Code
        │
git add
git commit
git push
        │
        ▼
GitHub
        │
        ▼
Citrus Server
git pull
```

---

### Git & GitHub

Configured a clean Git development environment.

Completed:

- ✅ Created GitHub repository
- ✅ Connected local repository
- ✅ Renamed default branch to `main`
- ✅ Removed accidental SFTP configuration from version control
- ✅ Added `.gitignore`
- ✅ Recreated repository with a clean commit history
- ✅ Successfully pushed the project to GitHub

---

### SSH Authentication

Configured SSH authentication for GitHub.

Completed:

- ✅ Generated SSH key for laptop
- ✅ Generated SSH key for Citrus server
- ✅ Added both public keys to GitHub
- ✅ Configured GitHub SSH authentication
- ✅ Switched Git remote from HTTPS to SSH

No more GitHub passwords or Personal Access Tokens required.

---

### Citrus Server

Re-synced the server with GitHub.

Completed:

- ✅ Fresh clone of the repository
- ✅ Clean Git history
- ✅ Ready for future deployments
- ✅ Server now follows GitHub as the single source of truth

---

### Lessons Learned

Today wasn't about adding new features.

It was about building a development workflow that can scale as Citrus grows.

Key takeaways:

- Treat the laptop as the development machine.
- Treat GitHub as the source of truth.
- Treat Citrus as the deployment server.
- Build features only after the workflow is reliable.

---

## Current Status

```text
Infrastructure      ✅ Complete
Remote Access       ✅ Complete
Development Shell   ✅ Complete
Git Workflow        ✅ Complete
GitHub Integration  ✅ Complete
SSH Authentication  ✅ Complete

Ready for:
⬜ Citrus Core v0.2
⬜ Dashboard
⬜ Service Manager
⬜ Bot Manager
⬜ Music Server
⬜ Flask API
```

---

## 📅 Day 3 — Core Development & Architecture Redesign

### Citrus Core Development

Implemented the first functional version of the Citrus Core.

Completed:

- ✅ Built the first interactive menu system
- ✅ Implemented Dashboard module
- ✅ Added Service Manager skeleton
- ✅ Added Git utilities module
- ✅ Added System module
- ✅ Created reusable helper utilities
- ✅ Integrated configuration management
- ✅ Successfully ran Citrus on the Android server

The project evolved from a folder structure into a working terminal application.

---

### Dashboard Development

Designed and implemented the first system dashboard.

Displayed:

- Hostname
- Current User
- Operating System
- Kernel Version
- Python Version
- Git Version
- Working Directory
- SSH Status
- Internet Status
- Local IP Address
- CPU Information
- RAM Usage
- Storage Usage
- Battery Percentage
- Runtime Information
- Citrus Version

---

### Platform Compatibility Testing

Extensively tested Citrus on the Samsung Galaxy J7 Max running Android 8.1 with Termux.

Verified:

- ✅ Dashboard rendering
- ✅ Navigation system
- ✅ SSH integration
- ✅ Network detection
- ✅ Storage detection
- ✅ Battery detection
- ✅ Python execution
- ✅ Git integration

---

### Bug Investigation

Performed extensive debugging of Android-specific behavior.

Investigated:

- CPU core detection inconsistency
- Android `/proc` permission limitations
- Uptime detection fallback
- Storage partition detection
- Termux-specific runtime behavior

Several platform-specific quirks were identified that required architectural improvements instead of incremental patches.

---

### Architecture Review

Reviewed the entire Citrus Core.

Identified several design problems:

- `utils.py` becoming a large utility module
- Dashboard mixing data collection and rendering
- Lack of centralized logging
- Weak separation of responsibilities
- Difficult debugging workflow
- Limited long-term scalability

Decided to stop feature development and redesign the Core before continuing.

---

### Citrus v1.0 Foundation Planning

Designed the next-generation Citrus architecture.

Planned improvements:

- Dedicated logging framework
- Event-driven architecture
- Session management
- Structured system collectors
- Modular page system
- Centralized configuration
- Runtime state management
- Custom exception hierarchy
- Improved dependency flow
- SOLID-based architecture

The goal shifted from **"making Citrus work"** to **"building a professional server operating platform."**

---

## Lessons Learned

Day 3 reinforced an important engineering principle:

> Building more features on a weak foundation only creates more technical debt.

Instead of continuing to patch platform-specific issues, the decision was made to redesign the Core into a scalable architecture capable of supporting future modules including Bots, Services, Git Management, Automation, Music Server, AI integrations, and a Web Dashboard.

---

## Current Status

```text
Infrastructure        ✅ Complete
Remote Access         ✅ Complete
Development Shell     ✅ Complete
Git Workflow          ✅ Complete
GitHub Integration    ✅ Complete
SSH Authentication    ✅ Complete

Citrus Core (v0.x)    ✅ Prototype Complete
Architecture Review   ✅ Complete
v1.0 Design           ✅ Finalized

Next Milestone:
⬜ Rewrite Core (v1.0)
⬜ Logging Framework
⬜ Event Bus
⬜ Session Manager
⬜ System Collectors
⬜ Modular Dashboard
```

---

## 📅 Day 4 — Citrus Core v1.0 Rewrite & Service Manager

### Citrus Core v1.0 Rewrite

Completely rebuilt the Citrus Core using the finalized modular architecture.

The rewrite was implemented in dependency order so that each layer could be independently compiled, imported, and tested before building the next layer.

Implemented architectural layers:

```text
Foundation
    │
    ├── Constants
    ├── Version
    ├── Exceptions
    └── Runtime State
    │
    ▼
Configuration
    │
    ▼
Logging & Events
    │
    ▼
Helpers & UI
    │
    ▼
System Information Collectors
    │
    ▼
Pages
    │
    ▼
Menu
    │
    ▼
Application
    │
    ▼
Citrus Entry Point
```

---

### Logging Framework

Implemented centralized structured logging.

Created dedicated log streams:

```text
logs/
├── system.log
├── events.log
├── errors.log
├── git.log
├── debug.log
└── services/
```

Logging now records:

- Application sessions
- Application start/stop events
- Page navigation
- Service activity
- Git activity
- Errors and exceptions
- Debug information

Session tracking includes:

- Session ID
- Hostname
- User
- Python version
- Platform
- Citrus version
- Runtime
- Exit reason

---

### Event System

Implemented structured application events.

Tracked events include:

- `APP_STARTED`
- `APP_STOPPED`
- `PAGE_OPENED`
- `PAGE_CLOSED`

Events are recorded independently from normal system logs, providing a dedicated history of Citrus activity.

---

### System Information Architecture

Separated system information collection from UI rendering.

Created dedicated collectors for:

- Identity
- Runtime
- Network
- Hardware
- Storage
- System information

Collectors return structured data while the UI is responsible for presentation.

This prevents pages such as the Dashboard from directly handling low-level system calls.

---

### Previous Android Bugs Resolved

The v1.0 architecture carried forward and properly addressed the major issues discovered during Day 3.

#### CPU Detection

The previous CPU detection implementation produced inconsistent results on the J7 Max.

The new implementation correctly parses `/proc/cpuinfo` and distinguishes the exact `processor` field from similarly named fields such as:

```text
processor
Processor
model name
```

The implementation was verified against the actual J7 Max `/proc/cpuinfo` output.

---

#### Uptime Detection

Android 8.1 / Termux denied access to:

```text
/proc/uptime
```

The collector now uses a fallback strategy:

```text
/proc/uptime
      │
      ├── Success → use value
      │
      └── Failure
             │
             ▼
        uptime command
             │
             ▼
         parse output
             │
             ▼
         Unavailable
```

This prevents the dashboard from crashing when Android restricts access to `/proc/uptime`.

---

#### Storage Detection

The previous implementation measured `/`, which on Android represented the read-only system partition.

The collector was changed to measure the Termux home environment instead.

This produces meaningful storage information for the actual Citrus runtime.

---

### Service Manager

Started the first complete Service Manager implementation.

The Service Manager is responsible for managing long-running background processes independently of the Citrus application itself.

Implemented:

- Service registry
- Service definitions
- Service discovery
- Service state tracking
- Process spawning
- PID tracking
- Running-state detection
- Service stopping
- Service restarting
- Service logging
- Persisted service state
- Service lifecycle management

Basic lifecycle:

```text
Discover
   │
   ▼
Service Definition
   │
   ▼
Start
   │
   ▼
Running
   │
   ├── Status
   ├── Logs
   ├── Stop
   └── Restart
```

---

### Test Service

Created a heartbeat service for validating the Service Manager.

The service continuously writes heartbeat messages to:

```text
logs/services/test_heartbeat.log
```

This provided a deterministic way to verify:

- Process creation
- PID tracking
- Running-state detection
- Log generation
- Stop behavior
- Restart behavior
- Service persistence

---

### Cross-Platform Compatibility Fix

During Windows testing, the Service Manager exposed a platform-specific problem:

```text
AttributeError:
module 'os' has no attribute 'WNOHANG'
```

The problem revealed that the runner contained multiple POSIX-specific assumptions.

Identified platform-specific operations included:

- `os.waitpid(..., os.WNOHANG)`
- `os.kill(pid, 0)`
- `os.killpg(...)`
- `os.getpgid(...)`

Instead of applying a single patch, the process runner was redesigned with explicit platform-specific execution paths.

#### POSIX / Termux

Uses:

- `waitpid`
- `WNOHANG`
- `kill`
- `killpg`
- `SIGTERM`
- `SIGKILL`
- `start_new_session`

#### Windows

Uses:

- Windows process status APIs
- `OpenProcess`
- `GetExitCodeProcess`
- Windows process groups
- `CTRL_BREAK_EVENT`
- `TerminateProcess`

The public Service Manager API remains unchanged while the underlying process management adapts to the operating system.

---

### Service Manager Testing

Performed comprehensive testing of the Service Manager.

Verified:

- ✅ Service discovery
- ✅ Service start
- ✅ Service status
- ✅ PID tracking
- ✅ Service log generation
- ✅ Service stop
- ✅ Service restart
- ✅ New PID after restart
- ✅ Service surviving Citrus exit
- ✅ Service rediscovery after Citrus restart
- ✅ Failed service startup handling
- ✅ POSIX runner behavior
- ✅ Windows runner behavior
- ✅ Process cleanup
- ✅ Full project compilation
- ✅ Interactive Citrus integration

The complete Service Manager test suite passed on the development environment.

---

### Termux / Android Validation

Deployed the updated Service Manager to **Citrus-01**.

Ran the Service Manager against the real Samsung Galaxy J7 Max / Android 8.1 / Termux environment.

Verified:

- ✅ Citrus launches successfully
- ✅ Services page opens without crashing
- ✅ Service discovery works
- ✅ Heartbeat service starts
- ✅ Actual Android process is created
- ✅ PID is detected
- ✅ Heartbeat logs continuously update
- ✅ Running status is detected
- ✅ Service stops correctly
- ✅ Service starts again correctly
- ✅ Service restarts with a new PID
- ✅ Service survives Citrus termination
- ✅ Service is rediscovered after Citrus relaunch
- ✅ Termux environment passes the Service Manager lifecycle tests

This was the first major feature validated on both the Windows development environment and the actual Citrus server.

---

### Git & Release Management

Completed the deployment cycle for the Service Manager update.

- ✅ Committed the Service Manager implementation
- ✅ Created a version tag
- ✅ Pushed the updated code to GitHub
- ✅ Pulled the updated version onto Citrus-01
- ✅ Verified the deployed version
- ✅ Validated the deployed code on Termux

---

### Day 4 Engineering Lessons

Day 4 reinforced several important engineering principles:

> **Platform compatibility must be designed, not patched after deployment.**

> **A server manager must manage processes independently from the application that controls them.**

> **A successful local test is not enough — the actual target environment must be tested.**

> **Logs are part of the system, not an afterthought.**

The Service Manager is now capable of forming the foundation for future Citrus capabilities such as:

- Python bots
- Automation workers
- Background APIs
- Website servers
- Scheduled jobs
- Monitoring services
- Data-processing workers

---

## Day 4 Final Status

```text
Infrastructure             ✅ Complete
Remote Access              ✅ Complete
Development Shell          ✅ Complete
Git Workflow               ✅ Complete
GitHub Integration         ✅ Complete
SSH Authentication         ✅ Complete

Citrus Core v1.0           ✅ Complete
Foundation Layer           ✅ Complete
Configuration              ✅ Complete
Logging Framework          ✅ Complete
Event System               ✅ Complete
System Collectors          ✅ Complete
Modular Pages              ✅ Complete
Application Layer          ✅ Complete

Service Manager            ✅ Complete
Service Registry           ✅ Complete
Process Runner             ✅ Complete
PID Tracking               ✅ Complete
Service Lifecycle          ✅ Complete
Service Logging            ✅ Complete
Persistence                ✅ Complete
Cross-Platform Support     ✅ Complete

Windows Testing            ✅ Passed
Termux Testing             ✅ Passed
Android/J7 Max Testing     ✅ Passed
Full Project Compilation   ✅ Passed

Current Release:
Citrus v1.1.0
```

---

## 🚀 Next Phase

### Day 5 — Service Manager Expansion

Planned direction:

⬜ Improve service configuration  
⬜ Expand service status information  
⬜ Improve service log viewer  
⬜ Add better process metadata  
⬜ Add service health monitoring  
⬜ Prepare Service Manager for real Python bots  
⬜ Begin Bot Manager architecture  
⬜ Connect Citrus services with future automation modules  

---

## 🍋 Citrus Development Philosophy

```text
Build the foundation.
        ↓
Test locally.
        ↓
Test on the real server.
        ↓
Find platform-specific problems.
        ↓
Fix the architecture.
        ↓
Deploy through Git.
        ↓
Verify again.
        ↓
Only then build the next feature.
```

Citrus is no longer just a collection of scripts.

It is becoming a modular server control platform.


---

## 📅 Day 5 — Service Manager Hardening

### State Persistence & Recovery

Hardened service runtime-state handling so corrupted persisted data is less likely to prevent Citrus from loading valid service state.

Implemented:

- Validate the top-level persisted-state structure.
- Discard malformed individual entries while retaining valid entries.
- Parse persisted restart counts strictly: only non-negative integers are accepted; missing values default to zero.
- Treat invalid optional timestamps as absent.
- Save runtime state atomically.
- Preserve lifecycle history when creating replacement runtime-state objects.

### Service Lifecycle Metadata

Expanded `ServiceRuntimeState` and the Services page with:

- Restart count
- Last exit timestamp
- Last stopped timestamp
- Last error information

The status check also handles a process whose PID no longer exists by marking the service as failed and recording exit information.

### Stop & Restart Reliability

Improved lifecycle operations to preserve state history and avoid accidental mutation of the previous state during a stop attempt.

- Snapshot the previous state before changing it to `STOPPING`.
- Restore the previous state if stopping fails.
- Preserve restart history across start/stop/restart operations.
- Increment the restart count once after a successful stop-and-start cycle.
- Preserve the runtime state file locally while removing it from version control.

### Service Registry Validation

Tightened service-definition validation:

- Service IDs must contain only letters, digits, underscores, or hyphens.
- `enabled` and `auto_restart` must be actual JSON booleans.
- Invalid definitions are rejected instead of being silently coerced.

### Test & Verification Record

Targeted checks performed during Day 5:

- ✅ `python -m compileall -q .` on Windows and Termux
- ✅ `git diff --check` and staged diff whitespace checks
- ✅ Restart-count parsing checks for valid and invalid values
- ✅ Corrupt persisted-state checks, including preservation of valid entries
- ✅ Mocked failed-stop recovery check
- ✅ Windows heartbeat start, restart, and stop smoke test
- ✅ Termux deployment at commit `c438685`
- ✅ Termux heartbeat start, restart, and stop smoke test
- ✅ Final Termux Git check confirmed `HEAD`, `main`, and `origin/main` at `c438685`, with a clean working tree

These were targeted validation checks, not a claim that a comprehensive automated regression suite exists.

### Git & Deployment

- ✅ Committed the hardening changes as `c438685` (`Harden service manager state handling`)
- ✅ Pushed the commit to GitHub
- ✅ Pulled the commit onto Citrus-01
- ✅ Confirmed the deployed commit matches `origin/main`
- ✅ Updated this development log and README for the Day 5 milestone
- ⬜ Commit the documentation update
- ⬜ Create and push the `v1.2.0` release tag after documentation is committed

### Day 5 Final Status

```text
Service-State Validation       ✅ Complete
Atomic State Persistence       ✅ Complete
Lifecycle Metadata             ✅ Complete
Stop/Restart State Recovery    ✅ Complete
Registry Validation            ✅ Complete
Windows Heartbeat Smoke Test   ✅ Passed
Termux Heartbeat Smoke Test    ✅ Passed
Targeted Persistence Checks    ✅ Passed
GitHub Deployment              ✅ Complete
Release Documentation          ✅ Drafts prepared
Release Tag                    ⬜ Pending
```

---

## 🍋 Next Phase

### Day 6 — Service Manager Edge-Case Testing

Recommended next focus:

- ⬜ Verify unexpected process-exit handling
- ⬜ Exercise malformed-state recovery against a disposable test state file
- ⬜ Test restart behavior when stopping or starting fails
- ⬜ Decide whether to build a repeatable automated regression suite
- ⬜ Only then move into the next major feature
