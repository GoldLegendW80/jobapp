"""Conservative native GET discovery. Labels alone never grant a search submission."""

from urllib.parse import parse_qsl, unquote, urlsplit

from browserd import detect

MARKER = "native_get"
TOOLS = frozenset(
    {
        "browser_navigate",
        "browser_snapshot",
        "browser_wait_for",
        "browser_type",
        "browser_click",
        "browser_select_option",
    }
)
QUERY = frozenset({"q", "query", "search", "keyword", "keywords"})
FIELDS = QUERY | {"location", "remote", "contract", "category", "page", "sort"}
ENDPOINTS = frozenset({"search", "jobs", "careers", "vacancies"})
WRITES = frozenset(
    {
        "apply",
        "application",
        "applications",
        "contact",
        "message",
        "messages",
        "profile",
        "account",
        "login",
        "signin",
        "register",
        "payment",
        "delete",
        "withdraw",
        "subscribe",
        "save",
        "update",
        "send",
    }
)
UNSUPPORTED = "unsupported_discovery"


def read_url(url: str, current: str, *, form: bool = False) -> bool:
    if not isinstance(url, str) or len(url) > 2000:
        return False
    try:
        parts, here = urlsplit(url), urlsplit(current)
        if (
            parts.scheme not in {"http", "https"}
            or parts.username
            or parts.password
            or "%" in parts.path
        ):
            return False
        if (parts.scheme, parts.hostname, parts.port) != (here.scheme, here.hostname, here.port):
            return False
        segments = unquote(parts.path).casefold().strip("/").split("/")
        return (
            not parts.fragment
            and not WRITES.intersection(segments)
            and (not form or segments[-1] in ENDPOINTS)
            and all(k in FIELDS for k, _ in parse_qsl(parts.query, keep_blank_values=True))
        )
    except (ValueError, TypeError):
        return False


def verdict(facts: object, tool: str, current: str) -> str | None:
    if not isinstance(facts, dict):
        return UNSUPPORTED
    action = (
        "type"
        if tool == "browser_type"
        else "check"
        if tool == "browser_select_option"
        else "click"
    )
    original = detect.verdict(facts, action=action, profile_edit=True)
    if original and original != "submit":
        return original
    if detect.says_submit(facts.get("names", []), profile_edit=True):
        return "submit"
    d = facts.get("discovery")
    if not isinstance(d, dict) or d.get("native") is not True:
        return UNSUPPORTED
    if facts.get("selector_hit") or facts.get("selector_error") or facts.get("custom_submit"):
        return "submit"
    if d.get("target") not in {"", "_self"}:
        return UNSUPPORTED
    if tool == "browser_click" and facts.get("tag") == "a":
        allowed = read_url(d.get("href", ""), current)
    else:
        fields = d.get("fields")
        allowed = (
            d.get("method") == "get"
            and read_url(d.get("action", ""), current, form=True)
            and isinstance(fields, list)
            and 0 < len(fields) <= 100
        )
        if allowed:
            allowed = any(f.get("name") in QUERY for f in fields if isinstance(f, dict))
            for f in fields:
                if not isinstance(f, dict) or f.get("name") not in FIELDS:
                    allowed = False
                    break
                if not (
                    (f.get("tag") == "input" and f.get("type") in {"text", "search", "hidden"})
                    or (f.get("tag") == "select" and f.get("type") == "select-one")
                ):
                    allowed = False
                    break
        if tool == "browser_type":
            allowed = (
                allowed
                and facts.get("tag") == "input"
                and facts.get("type") in {"text", "search"}
                and d.get("name") in FIELDS
            )
        elif tool == "browser_select_option":
            allowed = allowed and facts.get("tag") == "select" and d.get("name") in FIELDS
        elif tool == "browser_click":
            allowed = (
                allowed
                and facts.get("tag") in {"button", "input"}
                and facts.get("type") == "submit"
            )
        else:
            allowed = False
    if allowed and "point" in facts:
        return verdict(facts["point"], tool, current)
    return None if allowed else UNSUPPORTED
