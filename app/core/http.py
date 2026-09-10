"""Small HTTP response helpers."""

import re

_UNSAFE = re.compile(r'[\r\n"\\;]')


def content_disposition(kind: str, filename: str, fallback: str = "file") -> str:
    """A Content-Disposition header for a user-supplied filename.

    Strips the characters that could end the header value or the quoted
    string, so a stored filename can't inject headers or break the download.
    ``kind`` is ``inline`` or ``attachment``.
    """
    clean = _UNSAFE.sub("", filename or "").strip() or fallback
    return f'{kind}; filename="{clean}"'
