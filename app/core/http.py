"""Small HTTP response helpers."""

import re
import unicodedata
from urllib.parse import quote

_UNSAFE = re.compile(r'[\r\n"\;]')


def content_disposition(kind: str, filename: str, fallback: str = "file") -> str:
    """A Content-Disposition header for a user-supplied filename.

    Strips the characters that could end the header value or the quoted
    string, so a stored filename can't inject headers or break the download.
    ``kind`` is ``inline`` or ``attachment``.

    Header values must be Latin-1, so a name like "Café – plan.pdf" would
    otherwise crash the response with a 500. Non-ASCII names get an ASCII
    ``filename`` for old clients plus the RFC 5987 ``filename*`` that every
    current browser prefers.
    """
    clean = _UNSAFE.sub("", filename or "").strip() or fallback
    ascii_name = unicodedata.normalize("NFKD", clean).encode("ascii", "ignore").decode().strip() or fallback
    if ascii_name == clean:
        return f'{kind}; filename="{clean}"'
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(clean, safe='')}"
