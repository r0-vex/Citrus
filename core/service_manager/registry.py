"""
registry.py
===========
Discovers and loads service definitions from the services/ directory.
Read-only: the registry answers "what services are configured?" - it
never starts, stops, or otherwise touches processes. See runner.py
and manager.py for that.

Configuration format: one JSON file per service under services/,
e.g. services/atlas.json. JSON was chosen over a hand-rolled format
because it's stdlib-parseable (no third-party YAML dependency),
unambiguous, and still easy enough to hand-edit over Termux/nano.
Adding a new service never requires touching this module - just drop
another *.json file in services/.
"""

import json
import math
import re
import shlex
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core import config
from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.service_manager.models import HealthCheckConfig, ServiceDefinition

logger = get_logger(__name__)

_REQUIRED_FIELDS = ("id", "name", "command")

# Service ids end up in filesystem paths (logs/services/<id>.log), so
# they're restricted to a safe, unambiguous character set. This
# rejects path separators, "..", spaces, and empty strings, any of
# which could otherwise enable path traversal or produce a broken path.
_SERVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_valid_service_id(service_id: str) -> bool:
    """Check whether a service id is safe to use in filesystem paths.

    Args:
        service_id: The candidate id to check.

    Returns:
        True if the id matches ^[A-Za-z0-9_-]+$ exactly.
    """
    return bool(_SERVICE_ID_PATTERN.fullmatch(service_id))


def _validate_optional_bool(raw: dict, field_name: str, default: bool, source: Path) -> Optional[bool]:
    """Validate an optional field that must be a real JSON boolean.

    Rejects anything that isn't a literal JSON true/false - including
    the strings "true"/"false" and the integers 1/0 - since silently
    coercing those (e.g. via a bare bool() cast) can produce
    surprising results like bool("false") == True.

    Args:
        raw: The service definition dict.
        field_name: Which field to validate ("enabled" or "auto_restart").
        default: Value to use if the field is absent entirely.
        source: The file it came from, for the warning message.

    Returns:
        The field's boolean value (or `default` if the field is
        absent), or None if the field is present but not a real JSON
        boolean - the caller must treat None as "reject this
        definition".
    """
    if field_name not in raw:
        return default

    value = raw[field_name]
    if isinstance(value, bool):
        return value

    logger.warning(
        "Skipping %s: '%s' must be a JSON boolean (true/false), got %r",
        source,
        field_name,
        value,
    )
    return None


def _resolve_working_directory(raw_value: Optional[str]) -> Path:
    """Resolve a service's working_directory to an absolute path.

    Relative paths are resolved against the project root, so service
    definitions stay portable across different Termux installs
    instead of embedding a device-specific absolute path.

    Args:
        raw_value: The raw "working_directory" value from JSON, or
            None/empty to default to the project root itself.

    Returns:
        An absolute, user-expanded Path.
    """
    if not raw_value:
        return config.PROJECT_ROOT

    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = config.PROJECT_ROOT / path
    return path


def _parse_command(raw_command: object) -> Optional[List[str]]:
    """Normalize a "command" field into an argv-style list of strings.

    Accepts either a JSON array of strings (preferred - avoids shell
    parsing entirely) or a single string (split with shlex, so
    quoting works as a Termux user would expect).

    Args:
        raw_command: The raw "command" value from JSON.

    Returns:
        A list of strings, or None if the value isn't a usable command.
    """
    if isinstance(raw_command, str):
        parts = shlex.split(raw_command)
        return parts or None

    if isinstance(raw_command, list) and raw_command and all(isinstance(p, str) for p in raw_command):
        return raw_command

    return None


_ALLOWED_HEALTH_CHECK_TYPES = ("heartbeat_file", "http", "tcp")
_ALLOWED_HEALTH_CHECK_KEYS: Set[str] = {"type", "target", "timeout_seconds", "max_age_seconds"}
_LOOPBACK_HOSTS: Set[str] = {"127.0.0.1", "localhost", "::1"}


def _validate_numeric_bounded(
    value: object,
    field_name: str,
    min_val: float,
    max_val: float,
    source: Path,
) -> Optional[float]:
    """Validate a numeric configuration parameter (not bool, finite, within bounds)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        logger.warning(
            "Skipping %s: health_check '%s' must be a number, got %r",
            source, field_name, value,
        )
        return None
    val_float = float(value)
    if not math.isfinite(val_float) or val_float < min_val or val_float > max_val:
        logger.warning(
            "Skipping %s: health_check '%s' must be between %s and %s, got %r",
            source, field_name, min_val, max_val, value,
        )
        return None
    return val_float


def _parse_health_check(raw: dict, source: Path) -> Tuple[bool, Optional[HealthCheckConfig]]:
    """Parse and validate optional health_check field.

    Returns:
        (True, None) if "health_check" is absent entirely (valid, unmonitored).
        (True, HealthCheckConfig) if "health_check" is present and valid.
        (False, None) if "health_check" is present but malformed/invalid (caller skips).
    """
    if "health_check" not in raw:
        return True, None

    hc_raw = raw["health_check"]
    if not isinstance(hc_raw, dict):
        logger.warning("Skipping %s: 'health_check' must be a JSON object, got %r", source, hc_raw)
        return False, None

    unexpected_keys = set(hc_raw.keys()) - _ALLOWED_HEALTH_CHECK_KEYS
    if unexpected_keys:
        logger.warning("Skipping %s: 'health_check' contains unrecognized keys %s", source, unexpected_keys)
        return False, None

    hc_type = hc_raw.get("type")
    if not isinstance(hc_type, str) or hc_type not in _ALLOWED_HEALTH_CHECK_TYPES:
        logger.warning(
            "Skipping %s: 'health_check.type' must be one of %s, got %r",
            source, _ALLOWED_HEALTH_CHECK_TYPES, hc_type,
        )
        return False, None

    raw_target = hc_raw.get("target")
    if not isinstance(raw_target, (str, int)) or isinstance(raw_target, bool):
        logger.warning(
            "Skipping %s: 'health_check.target' must be a non-empty string or port integer",
            source,
        )
        return False, None

    target_str = str(raw_target).strip()
    if not target_str:
        logger.warning("Skipping %s: 'health_check.target' must not be empty", source)
        return False, None

    # Validate target per type
    if hc_type == "heartbeat_file":
        p = Path(target_str)
        if p.is_absolute() or ".." in p.parts:
            logger.warning(
                "Skipping %s: 'heartbeat_file' target must be a relative path without traversal, got %r",
                source, target_str,
            )
            return False, None

    elif hc_type == "http":
        try:
            parsed = urllib.parse.urlsplit(target_str)
        except Exception as exc:
            logger.warning("Skipping %s: invalid HTTP target URL %r: %s", source, target_str, exc)
            return False, None

        if parsed.scheme not in ("http", "https"):
            logger.warning("Skipping %s: HTTP target scheme must be 'http' or 'https', got %r", source, parsed.scheme)
            return False, None

        if parsed.username or parsed.password or "@" in parsed.netloc:
            logger.warning("Skipping %s: HTTP target must not contain credentials, got %r", source, target_str)
            return False, None

        hostname = (parsed.hostname or "").lower()
        if hostname not in _LOOPBACK_HOSTS:
            logger.warning(
                "Skipping %s: HTTP target host must be loopback (%s), got %r",
                source, _LOOPBACK_HOSTS, hostname,
            )
            return False, None

        try:
            port = parsed.port
        except ValueError:
            logger.warning("Skipping %s: invalid HTTP port in target %r", source, target_str)
            return False, None

        if port is not None and not (1 <= port <= 65535):
            logger.warning("Skipping %s: invalid HTTP port %r", source, port)
            return False, None

    elif hc_type == "tcp":
        host = "127.0.0.1"
        port_raw = ""
        if target_str.startswith("["):
            if "]:" not in target_str:
                logger.warning(
                    "Skipping %s: invalid bracketed IPv6 TCP target %r (missing ']:')",
                    source,
                    target_str,
                )
                return False, None
            host_part, _, port_part = target_str[1:].partition("]:")
            host = host_part.strip().lower()
            port_raw = port_part.strip()
        elif ":" in target_str:
            parts = target_str.split(":", 1)
            host = parts[0].strip().lower()
            port_raw = parts[1].strip()
        else:
            port_raw = target_str.strip()

        if host not in _LOOPBACK_HOSTS:
            logger.warning(
                "Skipping %s: TCP target host must be loopback (%s), got %r",
                source,
                _LOOPBACK_HOSTS,
                host,
            )
            return False, None

        try:
            port_val = int(port_raw)
        except ValueError:
            logger.warning("Skipping %s: invalid TCP port in target %r", source, target_str)
            return False, None

        if not (1 <= port_val <= 65535):
            logger.warning("Skipping %s: TCP port must be in range 1-65535, got %r", source, port_val)
            return False, None
        target_str = f"[{host}]:{port_val}" if ":" in host else f"{host}:{port_val}"

    # timeout_seconds: default 2.0 (0.1s to 30.0s)
    timeout = 2.0
    if "timeout_seconds" in hc_raw:
        parsed_timeout = _validate_numeric_bounded(hc_raw["timeout_seconds"], "timeout_seconds", 0.1, 30.0, source)
        if parsed_timeout is None:
            return False, None
        timeout = parsed_timeout

    # max_age_seconds: default 30.0 (0.5s to 3600.0s)
    max_age = 30.0
    if "max_age_seconds" in hc_raw:
        parsed_max_age = _validate_numeric_bounded(hc_raw["max_age_seconds"], "max_age_seconds", 0.5, 3600.0, source)
        if parsed_max_age is None:
            return False, None
        max_age = parsed_max_age

    return True, HealthCheckConfig(
        type=hc_type,
        target=target_str,
        timeout_seconds=timeout,
        max_age_seconds=max_age,
    )


def _parse_definition(raw: dict, source: Path) -> Optional[ServiceDefinition]:
    """Parse and validate one service definition dict.

    Invalid definitions are never raised - only logged and skipped,
    so one broken config file can't take down service discovery for
    every other service.

    Args:
        raw: The parsed JSON content of a service config file.
        source: The file it came from, for error messages.

    Returns:
        A ServiceDefinition, or None if the definition is invalid.
    """
    missing = [field_name for field_name in _REQUIRED_FIELDS if not raw.get(field_name)]
    if missing:
        logger.warning("Skipping %s: missing required field(s) %s", source, missing)
        return None

    raw_id = raw.get("id")
    if not isinstance(raw_id, str) or not _is_valid_service_id(raw_id):
        logger.warning(
            "Skipping %s: invalid service id %r (must be a non-empty string matching ^[A-Za-z0-9_-]+$)",
            source,
            raw_id,
        )
        return None

    command = _parse_command(raw["command"])
    if command is None:
        logger.warning("Skipping %s: 'command' must be a non-empty string or list of strings", source)
        return None

    environment = raw.get("environment", {})
    if not isinstance(environment, dict):
        logger.warning("Skipping %s: 'environment' must be an object", source)
        return None

    enabled = _validate_optional_bool(raw, "enabled", True, source)
    if enabled is None:
        return None

    auto_restart = _validate_optional_bool(raw, "auto_restart", False, source)
    if auto_restart is None:
        return None

    try:
        valid_hc, health_check = _parse_health_check(raw, source)
    except Exception as exc:
        logger.warning("Skipping %s: invalid health check configuration (%s)", source, exc)
        return None

    if not valid_hc:
        return None

    try:
        return ServiceDefinition(
            id=raw_id,
            name=str(raw["name"]),
            command=command,
            working_directory=_resolve_working_directory(raw.get("working_directory")),
            environment={str(key): str(value) for key, value in environment.items()},
            enabled=enabled,
            auto_restart=auto_restart,
            health_check=health_check,
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Skipping %s: %s", source, exc)
        return None


def discover() -> List[ServiceDefinition]:
    """Discover and load every valid service definition.

    Scans config.SERVICES_DIR for *.json files. Invalid files are
    skipped with a logged reason. Duplicate service ids keep the
    first one found (by filename order) and log a warning for the
    rest, rather than silently overwriting one with another.

    Returns:
        A list of valid ServiceDefinitions, in filename order.
    """
    services_dir = config.SERVICES_DIR
    if not services_dir.is_dir():
        logger.debug("Services directory does not exist: %s", services_dir)
        return []

    seen_ids: Dict[str, Path] = {}
    definitions: List[ServiceDefinition] = []

    for config_file in sorted(services_dir.glob("*.json")):
        try:
            raw = json.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping %s: could not parse JSON (%s)", config_file, exc)
            continue

        if not isinstance(raw, dict):
            logger.warning("Skipping %s: top-level JSON must be an object", config_file)
            continue

        definition = _parse_definition(raw, config_file)
        if definition is None:
            continue

        if definition.id in seen_ids:
            logger.warning(
                "Skipping %s: duplicate service id '%s' (already loaded from %s)",
                config_file,
                definition.id,
                seen_ids[definition.id],
            )
            continue

        seen_ids[definition.id] = config_file
        definitions.append(definition)
        emit(EventNames.SERVICE_DISCOVERED, service_id=definition.id, service_name=definition.name)
        logger.info("Discovered service '%s' (%s)", definition.id, config_file.name)

    return definitions


def get(service_id: str) -> Optional[ServiceDefinition]:
    """Look up a single service definition by id.

    Args:
        service_id: The service id to find.

    Returns:
        The matching ServiceDefinition, or None if not found.
    """
    for definition in discover():
        if definition.id == service_id:
            return definition
    return None
