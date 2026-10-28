"""Small bounded HTTP helpers shared by the local research APIs."""


def drain_rejected_body(handler, maximum):
    """Discard a small body without parsing it before sending an auth rejection.

    Closing a TCP socket with unread request bytes can discard the 401 response on
    Windows. Never drain unbounded input or wait indefinitely for a slow sender.
    Authorization has already failed; no payload is interpreted or persisted.
    """
    try:
        size = int(handler.headers.get('Content-Length', '0'))
    except ValueError:
        return
    if not 0 < size <= maximum:
        return
    previous = handler.connection.gettimeout()
    handler.connection.settimeout(1)
    try:
        handler.rfile.read(size)
    except OSError:
        pass
    finally:
        handler.connection.settimeout(previous)
