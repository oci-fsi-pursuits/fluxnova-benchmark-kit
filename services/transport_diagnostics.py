"""Optional metadata-only tracing; never record headers, bodies or error messages."""
import json
from pathlib import Path
import threading
import time


class TransportDiagnostics:
    EVENTS = {'connection_open', 'connection_closed', 'request_received', 'body_read',
              'response_started', 'response_sent', 'response_error', 'handler_error', 'http_error'}
    FIELDS = {'connection', 'route', 'status', 'bytes', 'error_class'}
    ROUTES = {'/node', '/execute', '/register', '/snapshot', '/observe'}

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse to append to an older experiment's trace.
        self.path.open('x', encoding='utf-8').close()
        self.lock = threading.Lock()
        self.sequence = 0
        self.connections = 0

    def connection(self):
        with self.lock:
            self.connections += 1
            return self.connections

    def emit(self, event, **fields):
        if event not in self.EVENTS or set(fields) - self.FIELDS:
            raise ValueError('unsupported_diagnostic_field')
        for key, value in fields.items():
            if key == 'route':
                fields[key] = value if value in self.ROUTES else 'other'
            elif key == 'error_class':
                # Exception type only; never include str(exception).
                if not isinstance(value, str) or not value.isidentifier() or len(value) > 80:
                    raise ValueError('invalid_exception_class')
            elif type(value) is not int or value < 0:
                raise ValueError('invalid_diagnostic_number')
        with self.lock:
            self.sequence += 1
            row = dict(seq=self.sequence, monotonic_ns=time.monotonic_ns(), event=event, **fields)
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, sort_keys=True) + '\n')
