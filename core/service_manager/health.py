"""
health.py
=========
Standard-library-only, deterministic health evaluators for Citrus services.

Provides on-demand health probes for managed services:
- heartbeat_file: file presence and modification-time freshness.
- http: loopback HTTP GET request expecting 2xx/3xx with safe redirect enforcement.
- tcp: loopback socket connectivity check.

Safety invariants:
- Never spawns subprocesses or executes shell commands.
- Never connects to non-loopback destinations.
- Enforces strict bounded timeouts.
- Never signals or controls processes.
"""

import math
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Set, Tuple

from core.logging.logger import get_logger
from core.service_manager.models import HealthCheckConfig, ServiceDefinition, ServiceHealth

logger = get_logger(__name__)

_ALLOWED_LOOPBACK_HOSTS: Set[str] = {"127.0.0.1", "localhost", "::1"}


class SafeLoopbackRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Enforce that HTTP redirects never leave loopback destinations."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if not _is_safe_loopback_url(parsed):
            logger.warning("Refusing unsafe redirect to non-loopback destination: %s", newurl)
            raise urllib.error.HTTPError(
                newurl, code, f"Unsafe redirect to non-loopback destination: {newurl}", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_safe_opener = urllib.request.build_opener(SafeLoopbackRedirectHandler)


def _is_safe_loopback_url(parsed: urllib.parse.SplitResult) -> bool:
    """Validate that a parsed URL points strictly to loopback without credentials."""
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        return False
    hostname = (parsed.hostname or "").lower()
    if hostname not in _ALLOWED_LOOPBACK_HOSTS:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port is not None and not (1 <= port <= 65535):
        return False
    return True


def check_heartbeat_file(target_path: Path, max_age_seconds: float) -> Tuple[ServiceHealth, str]:
    """Inspect a heartbeat file's existence and modification-time freshness.

    Args:
        target_path: Absolute or resolved path to the heartbeat file.
        max_age_seconds: Maximum allowed age in seconds before marking stale.

    Returns:
        (ServiceHealth, message) tuple.
    """
    try:
        if not target_path.exists():
            return ServiceHealth.UNHEALTHY, f"Heartbeat file does not exist: {target_path.name}"
        stat_res = target_path.stat()
        mtime = stat_res.st_mtime
        age = time.time() - mtime
        if age < 0:
            age = 0.0  # Clock skew tolerance
        if age > max_age_seconds:
            return ServiceHealth.UNHEALTHY, f"Heartbeat is stale: {age:.1f}s old (max {max_age_seconds:.1f}s)"
        return ServiceHealth.HEALTHY, f"Heartbeat fresh ({age:.1f}s ago)"
    except PermissionError as exc:
        logger.warning("Permission denied reading heartbeat file %s: %s", target_path, exc)
        return ServiceHealth.UNKNOWN, f"Permission denied reading heartbeat file: {exc}"
    except OSError as exc:
        logger.warning("Error checking heartbeat file %s: %s", target_path, exc)
        return ServiceHealth.UNKNOWN, f"Filesystem error checking heartbeat file: {exc}"


def check_http_endpoint(url: str, timeout_seconds: float) -> Tuple[ServiceHealth, str]:
    """Probe a loopback HTTP endpoint for a healthy (2xx/3xx) response.

    Args:
        url: Full loopback HTTP URL string.
        timeout_seconds: Connection and read timeout in seconds.

    Returns:
        (ServiceHealth, message) tuple.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        return ServiceHealth.UNKNOWN, f"Invalid HTTP probe URL: {exc}"
    except Exception as exc:
        return ServiceHealth.UNKNOWN, f"Invalid HTTP probe URL: {exc}"

    if port is not None and not (1 <= port <= 65535):
        return ServiceHealth.UNKNOWN, f"Invalid HTTP probe URL: port {port} out of range (1-65535)"

    if not _is_safe_loopback_url(parsed):
        return ServiceHealth.UNKNOWN, f"Refusing HTTP probe to non-loopback destination: {url}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Citrus-HealthCheck/1.0", "Accept": "*/*"},
        method="GET",
    )

    resp = None
    try:
        resp = _safe_opener.open(req, timeout=timeout_seconds)
        status = getattr(resp, "status", 200)
        # Read a tiny bounded snippet to ensure full receipt and close
        _ = resp.read(1024)
        if 200 <= status < 400:
            return ServiceHealth.HEALTHY, f"HTTP {status}"
        return ServiceHealth.UNHEALTHY, f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 600:
            return ServiceHealth.UNHEALTHY, f"HTTP {exc.code} ({exc.reason})"
        return ServiceHealth.UNHEALTHY, f"HTTP error {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return ServiceHealth.UNHEALTHY, f"HTTP probe timed out ({timeout_seconds:.1f}s)"
        return ServiceHealth.UNHEALTHY, f"HTTP connection failed: {reason}"
    except (socket.timeout, TimeoutError):
        return ServiceHealth.UNHEALTHY, f"HTTP probe timed out ({timeout_seconds:.1f}s)"
    except Exception as exc:
        return ServiceHealth.UNKNOWN, f"HTTP probe error: {exc}"
    finally:
        if resp is not None:
            try:
                resp.close()
            except OSError:
                pass


def check_tcp_port(target: str, timeout_seconds: float) -> Tuple[ServiceHealth, str]:
    """Probe a loopback TCP port for connection acceptance.

    Args:
        target: "host:port", "[host]:port", or "port".
        timeout_seconds: Socket connection timeout.

    Returns:
        (ServiceHealth, message) tuple.
    """
    host = "127.0.0.1"
    port_raw = ""
    if target.startswith("["):
        if "]:" not in target:
            return ServiceHealth.UNKNOWN, f"Invalid bracketed TCP target: {target}"
        host_part, _, port_part = target[1:].partition("]:")
        host = host_part.strip().lower()
        port_raw = port_part.strip()
    elif ":" in target:
        parts = target.split(":", 1)
        host = parts[0].strip().lower()
        port_raw = parts[1].strip()
    else:
        port_raw = target.strip()

    if host not in _ALLOWED_LOOPBACK_HOSTS:
        return ServiceHealth.UNKNOWN, f"Refusing TCP probe to non-loopback host: {host}"

    try:
        port = int(port_raw)
    except ValueError:
        return ServiceHealth.UNKNOWN, f"Invalid TCP port: {port_raw}"

    if not (1 <= port <= 65535):
        return ServiceHealth.UNKNOWN, f"TCP port out of range: {port}"

    display_target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout_seconds)
        return ServiceHealth.HEALTHY, f"TCP connection to {display_target} succeeded"
    except (socket.timeout, TimeoutError):
        return ServiceHealth.UNHEALTHY, f"TCP connection to {display_target} timed out ({timeout_seconds:.1f}s)"
    except (ConnectionRefusedError, ConnectionResetError):
        return ServiceHealth.UNHEALTHY, f"TCP connection to {display_target} refused"
    except OSError as exc:
        return ServiceHealth.UNHEALTHY, f"TCP connection to {display_target} failed: {exc}"
    except Exception as exc:
        return ServiceHealth.UNKNOWN, f"TCP probe error: {exc}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def evaluate_health(definition: ServiceDefinition) -> Tuple[ServiceHealth, Optional[str]]:
    """Evaluate application health according to the service's definition.

    Safe, synchronous probe. Never spawns processes, never signals processes.

    Args:
        definition: The service's static configuration.

    Returns:
        (ServiceHealth, Optional[health_message]) tuple.
    """
    config_hc = definition.health_check
    if config_hc is None:
        return ServiceHealth.NONE, None

    if config_hc.type == "heartbeat_file":
        target_path = Path(config_hc.target)
        if not target_path.is_absolute():
            target_path = definition.working_directory / target_path
        return check_heartbeat_file(target_path, config_hc.max_age_seconds)

    if config_hc.type == "http":
        return check_http_endpoint(config_hc.target, config_hc.timeout_seconds)

    if config_hc.type == "tcp":
        return check_tcp_port(config_hc.target, config_hc.timeout_seconds)

    return ServiceHealth.UNKNOWN, f"Unsupported health check type: {config_hc.type}"
