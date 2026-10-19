"""A person's own session (browser B7): "Look at my browser" (`takeover`) and "Sign in to a
site" (`signin`). Its Chromium runs on the person's profile with no debugging port, so nothing
but the person drives it and a site sees an ordinary browser while the person types.

Without CDP the session is watched through its process (SPEC-browser, "Chromium lifecycle"):
- the exit of Chromium ends the lease `browser_crashed` (chromium.Chromium._watch_exit), the
  profile left as it is;
- the memory guard reads the container's memory every MEMORY_EVERY_S and, past
  BROWSER_MEM_GUARD_PCT, ends the whole session `memory_guard` (no tab can be closed without
  CDP);
- the end asks Chromium to quit (SIGINT to its browser process, prep's `quit`), waits QUIT_S for
  it to write the profile, then kills its process group. SIGTERM is not used: Chromium then exits
  without writing the cookies of the last seconds (measured for B7, tasks/browser/todo.md).

The typed address of "Sign in to a site" (check_address) is http or https with a host, printable
ASCII, at most ADDRESS_MAX characters; it is given to Chromium after `--` (chromium.switches).
api/app/browser/address.py holds the api's copy of the rule; the tests run one table over both.

The control route of the profiles: `DELETE /profiles/{profile_id}` `{uid}` removes a profile's
directory ("Forget all logins", an account deleted; the api's removal queue): 204 once it is gone
(also when there was none), 409 `profile_in_use` while a lease of that profile runs, starts or
ends, 409 `profile_uid_mismatch` when the directory is not that uid's (nothing removed).
"""

import asyncio
import logging
import os
import re
import stat
import sys
from typing import Any
from urllib.parse import urlsplit

from browserd import chromium
from browserd import config as c
from browserd.leases import LeaseError

log = logging.getLogger("browserd")

QUIT_S = 10  # Chromium's clean exit after SIGINT, then its process group is killed
SETTLE_S = 1.0  # a Chromium that exits this soon after its start did not start
MEMORY_EVERY_S = 2
ADDRESS_MAX = 2048
# A host name of letters, digits and hyphens (an IPv4 address is one too), labels of 1 to 63.
LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def check_address(raw: Any) -> str | None:
    """The address to open, or None when it is not an http or https address with a host. Only
    printable ASCII without spaces nor backslashes passes, with no user name or password before
    the host, a port of 1 to 65535 if any; spaces around it are dropped. What is not understood
    is refused."""
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
    try:
        parts = urlsplit(text)
    except ValueError:  # an unbalanced bracket
        return None
    if "@" in parts.netloc or "[" in parts.netloc or parts.hostname is None:
        return None
    host = parts.hostname  # lower case
    if len(host) > 253 or not all(LABEL.fullmatch(label) for label in host.split(".")):
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port == 0 or parts.netloc.endswith(":"):
        return None
    return text


class PersonChromium(chromium.Chromium):
    """The Chromium of a person's own session: no debugging port, watched through its process."""

    async def start(self) -> "PersonChromium":
        await self.prepare()
        proc = await self.spawn(None, self.request.address or "about:blank")
        exited = asyncio.ensure_future(proc.wait())
        done, _ = await asyncio.wait({exited}, timeout=SETTLE_S)
        if done:
            await self._finish()
            raise RuntimeError("Chromium exited at its start")
        exited.cancel()
        self._tasks = [
            asyncio.create_task(self._watch_exit()),
            asyncio.create_task(self._watch_memory()),
        ]
        return self

    async def _watch_memory(self, reader: Any = chromium.memory) -> None:
        while not self.stopping:
            await asyncio.sleep(MEMORY_EVERY_S)
            figures = reader()
            if figures is not None and chromium.past_guard(figures, self.cfg.mem_guard_pct):
                self._event(event="session_ended", reason="memory", used_mib=figures[0] >> 20)
                if not self.stopping and self.on_end:
                    await self.on_end("memory_guard")
                return

    async def stop(self) -> None:
        self.stopping = True
        proc = self.proc
        if proc is not None and proc.returncode is None:
            await chromium.as_uid(
                self.request.uid, sys.executable, "-m", "browserd.prep", "quit", str(proc.pid)
            )
            try:
                await asyncio.wait_for(proc.wait(), QUIT_S)
            except TimeoutError:
                await self.kill()
                return
        await self._finish()


async def remove_profile(svc: Any, req: Any, match: re.Match[str]) -> tuple[int, Any]:
    profile_id = match["profile_id"]
    body = req.json()
    uid = body.get("uid") if isinstance(body, dict) else None
    if not isinstance(uid, int) or isinstance(uid, bool) or not c.UID_MIN <= uid <= c.UID_MAX:
        raise LeaseError(400, "bad_uid")
    manager = svc.manager
    if manager.uses(profile_id) or profile_id in manager.removing:
        raise LeaseError(409, "profile_in_use")
    path = f"{c.PROFILES}/{profile_id}"
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return 204, None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
        raise LeaseError(409, "profile_uid_mismatch")
    manager.removing.add(profile_id)
    try:
        code = await chromium.as_uid(
            uid, sys.executable, "-m", "browserd.prep", "rmprofile", path,
            timeout=chromium.MEASURE_S,
        )  # fmt: skip
    finally:
        manager.removing.discard(profile_id)
    if os.path.lexists(path):  # noqa: ASYNC240 - one stat
        log.warning("profile directory not removed (exit %d)", code)
        raise LeaseError(503, "not_removed")
    return 204, None


PROFILE_ID = r"(?P<profile_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
# (method, pattern, handler, body_max): browserd/app.py makes its Route of each.
ROUTES = [("DELETE", re.compile(rf"/profiles/{PROFILE_ID}"), remove_profile, 1024)]
