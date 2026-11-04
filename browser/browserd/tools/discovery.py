"""The website search of a sweep lease (browser B20, owner 2026-11-04: the search on the Jetson
works as Claude Code with Playwright CLI works on the owner's Mac).

A discovery is a `sweep` lease whose tool calls carry the api's marker (MARKER) and its scope
(`Scope`: the website's start address, and whether the person signed in to it, B17). It gets
every Playwright CLI command of TOOLS on any public http or https address, JavaScript on, service
workers allowed, at most three tabs, with no search rule: no frozen values to type, no host
family, no address shape, no search word, no start page constraint. What holds it is the line
(tools/line.py: never submit an application or a non search form, never send anything to a
person, never pay, sign out or change the account, never type a credential), browserd's egress
proxy and the host firewall (public addresses only), and the run's own Cancel.

The B13 to B19 guards this replaces (the saved values only typing, the host families and allow
lists, the posting and listing address shapes, composer and personal page refusals, the search
box and search word checks, the decline only consent rule, the second tab closed, SIGNED_IN_JS's
worker, service worker and socket restrictions) are in the history of this file and in
docs/decisions.md (2026-11-04).
"""

from dataclasses import dataclass

from browserd.tools import policy

MARKER = "native_get"
TOOLS = frozenset(
    {
        "browser_navigate",
        "browser_navigate_back",
        "browser_navigate_forward",
        "browser_snapshot",
        "browser_take_screenshot",
        "browser_click",
        "browser_hover",
        "browser_type",
        "browser_press_key",
        "browser_scroll",
        "browser_select_option",
        "browser_check",
        "browser_tabs",
        "browser_wait_for",
        "browser_evaluate",
        "browser_ask_person",
    }
)
UNSUPPORTED = "unsupported_discovery"
TABS_MAX = 3


@dataclass(frozen=True)
class Scope:
    start: str
    signed_in: bool = False

    @classmethod
    def parse(cls, raw: object) -> "Scope | None":
        """The api's scope: exactly `start` (an http or https address) and `signed_in`."""
        if not isinstance(raw, dict) or set(raw) - {"signed_in"} != {"start"}:
            return None
        start, signed_in = raw["start"], raw.get("signed_in", False)
        if type(signed_in) is not bool or not public_url(start):
            return None
        return cls(start, signed_in)


def public_url(url: object) -> bool:
    """An address the search may open: http or https, no user part, printable ASCII, any host
    (policy.url_verdict less its allow list; the egress proxy refuses a private address)."""
    return policy.url_verdict(url, ()) in (None, "host_not_allowed")
