"""Byte boundaries for retaining append-only collector JSONL exports."""
from pathlib import Path


def last_complete_jsonl_offset(path, *, block_bytes=65536):
    """Return the byte after the last LF in a snapshot of the open file.

    Export batches may exceed one block, including a partially written final
    batch. Scan backward in bounded memory rather than assuming a line length.
    Callers must separately validate JSON and producer timestamp coverage.
    """
    if type(block_bytes) is not int or block_bytes <= 0:
        raise ValueError('block_bytes must be a positive integer')
    with Path(path).open('rb') as stream:
        end = stream.seek(0, 2)
        while end:
            start = max(0, end - block_bytes)
            stream.seek(start)
            chunk = stream.read(end - start)
            if len(chunk) != end - start:
                raise ValueError('JSONL file was truncated while finding a boundary')
            boundary = chunk.rfind(b'\n')
            if boundary >= 0:
                return start + boundary + 1
            end = start
    raise ValueError('JSONL export contains no complete newline-terminated record')
