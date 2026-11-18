"""Local stand-ins for the ecosystem's stream-observation mesh primitives.

The open-source CLI has no observation producer: stream state is only
consumed in-process. These names keep ``monitor.py`` identical to the
ecosystem copy so future ports stay file-level diffs.
"""

# Marker key the monitor sets on emitted stream_info copies when a provider
# poll failed, so downstream consumers don't derive a bogus "offline" state.
PROVIDER_POLL_FAILED_KEY = "_provider_poll_failed"


class StreamObservationBacklogFull(Exception):
    """Backpressure signal: the observation layer cannot accept more events."""
