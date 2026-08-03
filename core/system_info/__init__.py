"""
system_info
===========
Aggregates all system-data collectors (identity, hardware, network,
storage, runtime) behind a single collect() entry point. Pages -
specifically pages/dashboard.py - should only ever call this
function, never the individual collector submodules directly.
"""

from typing import Dict

from core.system_info import hardware, identity, network, runtime, storage


def collect() -> Dict[str, dict]:
    """Collect all system information into one nested dictionary.

    Returns:
        A dict with keys: identity, hardware, network, storage,
        runtime - each mapping to that collector's own dict.
    """
    return {
        "identity": identity.collect(),
        "hardware": hardware.collect(),
        "network": network.collect(),
        "storage": storage.collect(),
        "runtime": runtime.collect(),
    }
