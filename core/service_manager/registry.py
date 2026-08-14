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
import shlex
from pathlib import Path
from typing import Dict, List, Optional

from core import config
from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.service_manager.models import ServiceDefinition

logger = get_logger(__name__)

_REQUIRED_FIELDS = ("id", "name", "command")


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

    command = _parse_command(raw["command"])
    if command is None:
        logger.warning("Skipping %s: 'command' must be a non-empty string or list of strings", source)
        return None

    environment = raw.get("environment", {})
    if not isinstance(environment, dict):
        logger.warning("Skipping %s: 'environment' must be an object", source)
        return None

    try:
        return ServiceDefinition(
            id=str(raw["id"]),
            name=str(raw["name"]),
            command=command,
            working_directory=_resolve_working_directory(raw.get("working_directory")),
            environment={str(key): str(value) for key, value in environment.items()},
            enabled=bool(raw.get("enabled", True)),
            auto_restart=bool(raw.get("auto_restart", False)),
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
