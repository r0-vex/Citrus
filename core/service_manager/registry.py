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
import re
import shlex
from pathlib import Path
from typing import Dict, List, Optional

from core import config
from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.service_manager.models import ServiceDefinition

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
        return ServiceDefinition(
            id=raw_id,
            name=str(raw["name"]),
            command=command,
            working_directory=_resolve_working_directory(raw.get("working_directory")),
            environment={str(key): str(value) for key, value in environment.items()},
            enabled=enabled,
            auto_restart=auto_restart,
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
