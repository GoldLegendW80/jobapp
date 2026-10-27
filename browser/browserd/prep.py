"""Run as the profile's own uid, never as root: `python -m browserd.prep prepare <profile dir>
<lease dir> <languages>`, `killpg <pgid>`, `kill <pid ...>`, `killuid`, `quit <pid>`,
`rmtree <lease dir>`, `measure <profile dir> <trim bytes>`, `rmprofile <profile dir>`. browserd
is root with SETUID and SETGID only (no DAC_OVERRIDE, no CHOWN, no KILL), so it cannot read or
write a profile nor signal its Chromium itself; this child does it under the profile's uid, then
exits.

- `killuid`: SIGKILL to every process of the profile's uid at once (kill(-1), which no fork
  outruns), this one excepted; refused under any uid outside the profile range;
- `quit`: SIGINT to Chromium's browser process alone, its clean exit (measured for B7:
  SIGTERM, to the process or to its group, exits without writing the cookies of the last
  seconds, and SIGINT writes them, a cookie without an expiry date included);
- `measure`: the profile's size in bytes (regular files, links never followed), printed; over
  the trim size its caches (CACHES) are removed first, never its cookies, storage or session;
- `rmprofile`: the profile directory removed whole ("Forget all logins", an account deleted);
- `sitecookies <profile dir> <host>`: `1` when the profile's cookie store holds a cookie of that
  host or one of its parent domains that has not expired (B17, a website's login saved), `0`
  otherwise. Only the host and expiry columns are read, never a name nor a value; the store is
  opened read only and immutable, so nothing of the profile changes.

- the profile directory, created with mode 0700 by its uid (/profiles is 1733: anyone may create
  an entry, nobody may list it, only the owner may remove theirs);
- the Singleton* lock files removed: a lock left by another host name (a container recreated on
  the persisted volume) blocks Chromium for 20 s and more (B1). browserd runs no other Chromium
  when it calls this;
- the tabs of the last session removed (SAVED_TABS): every session opens on its own address
  alone, so it never starts past the cap of TABS_MAX tabs (issue #488);
- the preferences Chromium reads at start: the languages (`--lang` is ignored on Linux, B1),
  continue where you left off (session cookies survive a restart), no password saving, no card
  autofill, downloads into the lease's directory without a prompt; in Local State, the remote
  debugging switch of chrome://inspect off, no saved chrome://flags entry and no policy set
  saved by chrome://policy/test;
- the lease's directory, mode 0700, for downloads.
"""

import contextlib
import json
import os
import re
import shutil
import signal
import stat
import sys

from browserd.config import UID_MAX, UID_MIN

LOCKS = ("SingletonLock", "SingletonSocket", "SingletonCookie")
PROFILE = re.compile(r"/profiles/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Where chrome://flags keeps its entries in Local State (the names in Chromium 155's binary).
FLAGS = ("enabled_labs_experiments", "enabled_labs_experiments_origin_lists")
# The policies chrome://policy/test's "Restart and apply" saves for the next start.
TEST_POLICIES = "local_test_policies_for_next_startup"
# Where Chromium keeps the tabs of the last session (Default/Sessions since Chromium 100, the four
# files before it). With "continue where you left off" it reopens them at the next start beside
# the address it is given, so a profile gained one tab per lease, past TABS_MAX (issue #488).
SAVED_TABS = (
    "data/Default/Sessions",
    "data/Default/Current Session",
    "data/Default/Current Tabs",
    "data/Default/Last Session",
    "data/Default/Last Tabs",
)
# What Chromium rebuilds by itself: its disk caches (the cache lives in the profile, which is
# not under XDG_CONFIG_HOME), shader caches, crash reports. Cookies, Local Storage, IndexedDB,
# Session Storage and Preferences are never in this list (Sessions, the saved tabs, go before
# every start: SAVED_TABS).
CACHES = (
    "cache",
    "data/Default/Cache",
    "data/Default/Code Cache",
    "data/Default/GPUCache",
    "data/Default/DawnGraphiteCache",
    "data/Default/DawnWebGPUCache",
    "data/Default/Service Worker/CacheStorage",
    "data/Default/Service Worker/ScriptCache",
    "data/GrShaderCache",
    "data/GraphiteDawnCache",
    "data/ShaderCache",
    "data/component_crx_cache",
    "data/Crashpad",
)


def merge(into: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            merge(into[key], value)
        else:
            into[key] = value
    return into


def remove(path: str) -> None:
    """A directory removed whole, a file or a link unlinked (a link is never followed), nothing
    when it is not there."""
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


def prepare(profile: str, lease: str, languages: str) -> None:
    os.umask(0o077)
    for path in (
        profile,
        f"{profile}/data",
        f"{profile}/data/Default",
        lease,
        f"{lease}/downloads",
    ):
        os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(profile, 0o700)  # an existing directory too: never readable by another uid
    for name in LOCKS:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"{profile}/data/{name}")
    for saved in SAVED_TABS:
        remove(os.path.join(profile, saved))
    path = f"{profile}/data/Default/Preferences"
    try:
        with open(path, encoding="utf-8") as handle:
            prefs = json.load(handle)
    except (FileNotFoundError, ValueError):
        prefs = {}
    merge(
        prefs,
        {
            "intl": {"accept_languages": languages, "selected_languages": languages},
            "session": {"restore_on_startup": 1},
            "credentials_enable_service": False,
            "profile": {"password_manager_enabled": False},
            "autofill": {"credit_card_enabled": False},
            "download": {
                "default_directory": f"{lease}/downloads",
                "prompt_for_download": False,
            },
            "translate": {"enabled": False},
            # No WebRTC UDP outside the proxy: Debian's Chromium ignores the switch
            # --force-webrtc-ip-handling-policy; the image's managed policy says it too.
            "webrtc": {"ip_handling_policy": "disable_non_proxied_udp"},
        },
    )
    tmp = f"{path}.browserd"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(prefs, handle)
    os.replace(tmp, path)
    local_state_reset(f"{profile}/data/Local State")


def local_state_reset(path: str) -> None:
    """chrome://inspect's "Allow remote debugging" is kept in Local State and opens a DevTools
    port at every later start, with no --remote-debugging-port (measured for B7): it is turned
    off. The chrome://flags entries saved there keep applying once the page is blocked, and a
    policy set saved by chrome://policy/test replaces the image's at the next start: both are
    dropped. A Local State that is missing or not JSON holds no such setting."""
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (FileNotFoundError, ValueError):
        return
    if not isinstance(state, dict):
        return
    merge(state, {"devtools": {"remote_debugging": {"user-enabled": False}}})
    if isinstance(state.get("browser"), dict):
        for name in FLAGS:
            state["browser"].pop(name, None)
    state.pop(TEST_POLICIES, None)
    tmp = f"{path}.browserd"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(tmp, path)


def size_of(root: str) -> int:
    """Bytes of the regular files under root; a link is never followed, an entry that vanishes
    while it is read counts nothing."""
    total = 0
    for folder, _, files in os.walk(root):
        for name in files:
            try:
                info = os.lstat(os.path.join(folder, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def measure(profile: str, trim_bytes: int) -> int:
    size = size_of(profile)
    if size > trim_bytes:
        for cache in CACHES:
            path = os.path.join(profile, cache)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
        size = size_of(profile)
    return size


# Chromium's cookie times: microseconds since 1601-01-01 UTC.
CHROME_EPOCH_S = 11644473600
COOKIE_STORES = ("data/Default/Network/Cookies", "data/Default/Cookies")


def site_domains(host: str) -> list[str]:
    """The host_key values a cookie sent to `host` may have: the host, and the host and each
    parent domain of two labels or more with a leading dot."""
    labels = host.lower().strip(".").split(".")
    parents = [".".join(labels[i:]) for i in range(len(labels) - 1)] or [host.lower()]
    return [host.lower(), *(f".{d}" for d in parents)]


def site_cookies(profile: str, host: str, now_s: float) -> bool:
    import sqlite3

    domains = site_domains(host)
    now = int((now_s + CHROME_EPOCH_S) * 1_000_000)
    marks = ",".join("?" * len(domains))
    for store in COOKIE_STORES:
        path = os.path.join(profile, store)
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        db = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            row = db.execute(
                f"SELECT 1 FROM cookies WHERE host_key IN ({marks})"  # noqa: S608  marks only
                " AND (has_expires = 0 OR expires_utc > ?) LIMIT 1",
                (*domains, now),
            ).fetchone()
        finally:
            db.close()
        if row is not None:
            return True
    return False


if __name__ == "__main__":
    if os.getuid() == 0:
        sys.exit("prep runs as the profile's uid, never as root")
    command, args = sys.argv[1], sys.argv[2:]
    if command == "prepare":
        prepare(*args)
    elif command == "killpg":
        os.killpg(int(args[0]), signal.SIGKILL)
    elif command == "kill":
        for pid in args:
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(pid), signal.SIGKILL)
    elif command == "killuid" and UID_MIN <= os.getuid() <= UID_MAX:
        # kill(-1): every process this uid may signal but this one, in one pass forks cannot outrun.
        with contextlib.suppress(ProcessLookupError):
            os.kill(-1, signal.SIGKILL)
    elif command == "quit":
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(args[0]), signal.SIGINT)
    elif command == "rmtree" and args[0].startswith("/leases/"):
        shutil.rmtree(args[0], ignore_errors=True)
    elif command == "measure" and PROFILE.fullmatch(args[0]):
        print(measure(args[0], int(args[1])))
    elif command == "rmprofile" and PROFILE.fullmatch(args[0]):
        if os.path.lexists(args[0]):
            shutil.rmtree(args[0])
    elif command == "sitecookies" and PROFILE.fullmatch(args[0]):
        import time

        print(1 if site_cookies(args[0], args[1], time.time()) else 0)
    else:
        sys.exit(f"unknown command {command}")
