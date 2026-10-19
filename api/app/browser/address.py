"""The address a person types in "Sign in to a site" (browser B7), checked before anything
starts: an http or https address with a host, else 422 `bad_address`.

The rule is browserd's own (browser/browserd/signin.py, `check_address`, which checks it again):
printable ASCII without spaces nor backslashes, at most ADDRESS_MAX characters, the scheme http
or https followed by `://`, no user name or password before the host, a host of letters, digits
and hyphens in labels of 1 to 63 (an IPv4 address is one), a port of 1 to 65535 if any. Spaces
around it are dropped. Whatever is not understood is refused, so a value can never start
Chromium on a switch (`--remote-debugging-port=9222`, `-a`), a script (`javascript:`), a file or
a page of the browser itself. A host written in another script must be typed in its `xn--`
form. api/tests/browser/test_b7_address.py runs one table over both copies.
"""

import re
from typing import Any
from urllib.parse import urlsplit

ADDRESS_MAX = 2048
LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def check_address(raw: Any) -> str | None:
    """The address to open, or None when it is not an http or https address with a host."""
    if not isinstance(raw, str):
        return None
    text = raw.strip(" \t\r\n")
    if not text or len(text) > ADDRESS_MAX:
        return None
    if any(not 0x21 <= ord(ch) <= 0x7E or ch == "\\" for ch in text):
        return None
    head, sep, _ = text.partition("://")
    if not sep or head.lower() not in ("http", "https"):
        return None
    parts = urlsplit(text)
    if "@" in parts.netloc or parts.hostname is None:
        return None
    host = parts.hostname
    if len(host) > 253 or not all(LABEL.fullmatch(label) for label in host.split(".")):
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port == 0 or parts.netloc.endswith(":"):
        return None
    return text
