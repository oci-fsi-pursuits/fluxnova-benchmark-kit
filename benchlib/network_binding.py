"""Post-reset endpoint identity check for a disposable internal Docker cell."""


def require_endpoint_bindings(expected, actual):
    """Reject stale frozen URLs after a container restart, before any load."""
    if (set(expected) != {'engine', 'ledger'} or set(actual) != set(expected)
            or any(not isinstance(value, str) or not value for value in expected.values())
            or expected != actual):
        raise ValueError('Live engine/ledger endpoints differ from the frozen configuration')
