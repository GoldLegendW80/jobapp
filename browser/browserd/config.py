"""browserd's settings, read once from its environment (compose.yml's `browser` service).

The container gets these and nothing else of the application: no database, Redis or model
address, no key. An empty BROWSER_CONTROL_SECRET is not an error at start (the stack starts
before the api holds one) but every request of the control API is then refused.
"""

import os
import re
from dataclasses import dataclass, field

# Active seconds a lease may run, per purpose (SPEC-browser, "Leases"): 15 minutes for the apply
# purposes, 30 for a person's own session (Apply myself's `apply_manual` included, apply A9).
# BROWSER_LEASE_MAX_S overrides some of them as
# "purpose=seconds,purpose=seconds" (A15 measures a Workday wizard and proposes its figure).
LEASE_MAX_S = {
    "takeover": 1800,
    "signin": 1800,
    "apply_manual": 1800,
    "apply_fill": 900,
    "apply_submit": 900,
    "profile_edit": 900,
    "full_auto": 900,
    # A19: One click Apply's agent fills, writes, uploads and clicks Submit itself, as on the
    # owner's Mac; an hour of active time, the idle timer still ends a stuck one.
    "apply_agent": 3600,
    "apply_review": 3600,  # A20: Review first's agent, its fill and the wait for the Confirm
    "sweep": 86_400,  # B20: as long as the search takes; the idle timer still ends a stuck one
}
PURPOSES = tuple(LEASE_MAX_S)

CONTROL_PORT = 9300
PROXY_PORT = 3128
DISPLAY = ":1"
SCREEN = "1440x900x24"
# Two browsers at once (owner, 2026-11-06): BROWSER_SLOTS windows of one person's Chromium, side
# by side on one display as wide as the slots, each slot's live view clipped to its own region.
SLOT_W, SLOT_H = 1440, 900
SLOTS_MAX = 2  # the owner chose two (2026-11-06); a third does not fit the 2048m container
PROFILES = "/profiles"
# browserd's record of which profile each uid belongs to (chromium.bind_uid), root's, 0700.
UIDS = "/profiles/.uids"
LEASES = "/leases"
STATE_DIR = "/tmp/browserd"  # noqa: S108 - the container's own /tmp, 0700 root
# Linux uids of the profiles: 20000 plus a counter (SPEC-browser), allocated by the api (B3).
UID_MIN, UID_MAX = 20000, 59999
# The display's own user, uid 1000 of the image: Xvfb never runs as root nor as a profile's uid.
DISPLAY_UID = 1000
TABS_MAX = 3
CDP_ANSWER_S = 10  # no CDP answer in 10 s: the lease ends `browser_crashed`
START_S = 30  # Chromium up to a usable CDP (B1: 0.44 to 0.85 s, 20 s and more on a stale lock)
CLOSE_S = 5  # Browser.close to exit (B1: 0.32 s), then the process group is killed
NAVIGATION_S = 30  # the tools' default navigation timeout, a failure result past it


def screen(slots: int) -> str:
    """Xvfb's screen for that many slots: one 1440 by 900 region per slot, left to right."""
    return f"{SLOT_W * slots}x{SLOT_H}x24"


def clip(slot: int) -> str:
    """x11vnc's -clip of a slot's region of the display."""
    return f"{SLOT_W}x{SLOT_H}+{SLOT_W * slot}+0"


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def lease_max_table(raw: str) -> dict[str, int]:
    table = dict(LEASE_MAX_S)
    for item in filter(None, (part.strip() for part in raw.split(","))):
        purpose, _, seconds = item.partition("=")
        if purpose.strip() not in table or not re.fullmatch(r"\d{2,5}", seconds.strip()):
            raise ValueError(f"BROWSER_LEASE_MAX_S: {item!r} is not purpose=seconds")
        table[purpose.strip()] = int(seconds)
    return table


@dataclass(frozen=True)
class Config:
    secret: bytes = b""
    lease_max_s: dict[str, int] = field(default_factory=lambda: dict(LEASE_MAX_S))
    idle_s: int = 300
    session_max_s: int = 1800
    mem_guard_pct: int = 90
    # Host names the egress proxy lets through although they resolve to a private address: the
    # test stack's pages server only (tests/browser.compose.yml). Empty in compose.yml, and
    # refused at start under APP_ENV=production (review of PR #294, issue #230).
    proxy_allow: frozenset[str] = frozenset()
    linkedin_stub_base_url: str = ""  # exact test origin and /linkedin prefix, never production
    # One browser unless BROWSER_SLOTS says more (compose.yml sets 2): the tests' default.
    slots: int = 1

    @classmethod
    def from_env(cls) -> "Config":
        allow = os.environ.get("BROWSER_PROXY_ALLOW_PRIVATE", "")
        names = frozenset(h.strip().lower() for h in allow.split(",") if h.strip())
        linkedin_stub = os.environ.get("BROWSER_LINKEDIN_STUB_BASE_URL", "").rstrip("/")
        if (names or linkedin_stub) and os.environ.get(
            "APP_ENV", ""
        ).strip().lower() == "production":
            named = "BROWSER_PROXY_ALLOW_PRIVATE" if names else "BROWSER_LINKEDIN_STUB_BASE_URL"
            raise ValueError(f"{named} is for test stacks, refused when APP_ENV=production")
        return cls(
            secret=os.environ.get("BROWSER_CONTROL_SECRET", "").strip().encode(),
            lease_max_s=lease_max_table(os.environ.get("BROWSER_LEASE_MAX_S", "")),
            idle_s=_int("BROWSER_IDLE_S", 300, 1, 3600),
            session_max_s=_int("BROWSER_SESSION_MAX_S", 1800, 1, 7200),
            mem_guard_pct=_int("BROWSER_MEM_GUARD_PCT", 90, 10, 99),
            proxy_allow=names,
            linkedin_stub_base_url=linkedin_stub,
            slots=_int("BROWSER_SLOTS", 2, 1, SLOTS_MAX),
        )
