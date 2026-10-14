"""
registry.py
===========
Discovers and strictly validates bot manifests from the bots/ directory.
Read-only: discovers and answers "what bots are configured?" - does not
start, stop, or manipulate OS processes.

Configuration format: JSON manifests in bots/*.json, e.g. bots/atlas.json.
Python scripts or other files in bots/ (like bots/atlas.py) are ignored
by the *.json glob.
"""

import json
import re
import shlex
from pathlib import Path
from typing import Dict, List, Optional

from core import config
from core.bot_manager.models import BotDefinition
from core.logging.events import EventNames, emit
from core.logging.logger import get_logger

logger = get_logger(__name__)

_REQUIRED_FIELDS = ("id", "name", "command")

# Bot IDs end up in filesystem paths (logs/bots/<id>.log), so they are
# restricted to a safe, unambiguous character set. Rejects traversal (..),
# path separators, spaces, and empty strings.
_BOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_valid_bot_id(bot_id: str) -> bool:
    """Check whether a bot id is safe to use in filesystem paths.

    Args:
        bot_id: Candidate ID string.

    Returns:
        True if the ID strictly matches ^[A-Za-z0-9_-]+$.
    """
    return bool(_BOT_ID_PATTERN.fullmatch(bot_id))


def _validate_optional_bool(raw: dict, field_name: str, default: bool, source: Path) -> Optional[bool]:
    """Validate an optional field that must be a strict JSON boolean.

    Rejects strings like "true"/"false" and integers 1/0.

    Args:
        raw: Raw JSON dictionary.
        field_name: Field name to validate ("enabled" or "auto_restart").
        default: Fallback value if key is absent.
        source: Manifest file path for diagnostic logging.

    Returns:
        Validated boolean or default, or None if present but not a strict boolean.
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
    """Resolve a bot's working_directory to an absolute path against PROJECT_ROOT.

    Args:
        raw_value: Raw working_directory string or None/empty.

    Returns:
        Absolute resolved Path.
    """
    if not raw_value:
        return config.PROJECT_ROOT

    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = config.PROJECT_ROOT / path
    return path


def _parse_command(raw_command: object) -> Optional[List[str]]:
    """Normalize a command field into an argv-style list of strings.

    Accepts an array of strings or a single string split via shlex.

    Args:
        raw_command: Raw command object from JSON.

    Returns:
        List of command arguments, or None if invalid or empty.
    """
    if isinstance(raw_command, str):
        try:
            parts = shlex.split(raw_command)
            return parts or None
        except ValueError:
            return None

    if isinstance(raw_command, list) and raw_command and all(isinstance(p, str) for p in raw_command):
        return raw_command

    return None


def _parse_definition(raw: dict, source: Path) -> Optional[BotDefinition]:
    """Parse and strictly validate one bot definition dictionary.

    Args:
        raw: Parsed JSON content.
        source: File path for diagnostic messages.

    Returns:
        A validated BotDefinition or None if malformed.
    """
    missing = [field_name for field_name in _REQUIRED_FIELDS if not raw.get(field_name)]
    if missing:
        logger.warning("Skipping %s: missing required field(s) %s", source, missing)
        return None

    raw_id = raw.get("id")
    if not isinstance(raw_id, str) or not _is_valid_bot_id(raw_id):
        logger.warning(
            "Skipping %s: invalid bot id %r (must be a non-empty string matching ^[A-Za-z0-9_-]+$)",
            source,
            raw_id,
        )
        return None

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        logger.warning("Skipping %s: 'name' must be a non-empty string", source)
        return None

    command = _parse_command(raw["command"])
    if command is None:
        logger.warning("Skipping %s: 'command' must be a non-empty string or list of strings", source)
        return None

    environment = raw.get("environment", {})
    if not isinstance(environment, dict):
        logger.warning("Skipping %s: 'environment' must be an object", source)
        return None

    # Ensure environment dictionary is safely converted without logging values
    safe_env = {str(k): str(v) for k, v in environment.items()}

    enabled = _validate_optional_bool(raw, "enabled", True, source)
    if enabled is None:
        return None

    auto_restart = _validate_optional_bool(raw, "auto_restart", False, source)
    if auto_restart is None:
        return None

    try:
        return BotDefinition(
            id=raw_id,
            name=name.strip(),
            command=command,
            working_directory=_resolve_working_directory(raw.get("working_directory")),
            environment=safe_env,
            enabled=enabled,
            auto_restart=auto_restart,
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Skipping %s: %s", source, exc)
        return None


def discover() -> List[BotDefinition]:
    """Discover and return all valid bot definitions in bots/*.json.

    Scans config.BOTS_DIR for *.json manifests. Invalid files are logged
    and skipped. Duplicate bot IDs retain the first file in sorted filename
    order and log a warning for duplicates.

    Returns:
        List of valid BotDefinitions in filename sort order.
    """
    bots_dir = config.BOTS_DIR
    if not bots_dir.is_dir():
        logger.debug("Bots directory does not exist: %s", bots_dir)
        return []

    seen_ids: Dict[str, Path] = {}
    definitions: List[BotDefinition] = []

    for manifest_file in sorted(bots_dir.glob("*.json")):
        try:
            raw = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping %s: could not parse JSON (%s)", manifest_file, exc)
            continue

        if not isinstance(raw, dict):
            logger.warning("Skipping %s: top-level JSON must be an object", manifest_file)
            continue

        definition = _parse_definition(raw, manifest_file)
        if definition is None:
            continue

        if definition.id in seen_ids:
            logger.warning(
                "Skipping %s: duplicate bot id '%s' (already loaded from %s)",
                manifest_file,
                definition.id,
                seen_ids[definition.id],
            )
            continue

        seen_ids[definition.id] = manifest_file
        definitions.append(definition)
        emit(EventNames.BOT_DISCOVERED, bot_id=definition.id, bot_name=definition.name)
        logger.info("Discovered bot '%s' (%s)", definition.id, manifest_file.name)

    return definitions


def get(bot_id: str) -> Optional[BotDefinition]:
    """Look up a single bot definition by ID.

    Args:
        bot_id: Bot identifier.

    Returns:
        Matching BotDefinition or None if not found.
    """
    for definition in discover():
        if definition.id == bot_id:
            return definition
    return None
