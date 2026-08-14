"""
test_heartbeat.py
==================
A deliberately trivial TEST service for exercising the Service
Manager end-to-end - NOT a production service. Writes one heartbeat
line to stdout every few seconds until it's terminated.

Safe by design: negligible CPU/memory, no file writes outside its own
stdout (which Citrus redirects to logs/services/test_heartbeat.log),
no network activity, and no interaction with any other part of the
device. Runs indefinitely only because the Service Manager tracks and
can stop it - it is never meant to be launched outside of Citrus.
"""

import sys
import time
from datetime import datetime

INTERVAL_SECONDS = 2


def main() -> None:
    """Print one heartbeat line every INTERVAL_SECONDS, forever."""
    count = 0
    while True:
        count += 1
        print(f"heartbeat #{count} at {datetime.now().isoformat(timespec='seconds')}", flush=True)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
