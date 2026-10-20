"""Run as the profile's own uid, never as root: `python -m browserd.prep prepare <profile dir>
<lease dir> <languages>`, `killpg <pgid>`, `kill <pid ...>`, `quit <pid>`, `rmtree <lease dir>`,
`measure <profile dir> <trim bytes>`, `rmprofile <profile dir>`. browserd is root with SETUID
and SETGID only (no DAC_OVERRIDE, no CHOWN, no KILL), so it cannot read or write a profile nor
signal its Chromium itself; this child does it under the profile's uid, then exits.

- `quit`: SIGINT to Chromium's browser process alone, its clean exit (measured for B7:
  SIGTERM, to the process or to its group, exits without writing the cookies of the last
  seconds, and SIGINT writes them, a cookie without an expiry date included);
- `measure`: the profile's size in bytes (regular files, links never followed), printed; over
  the trim size its caches (CACHES) are removed first, never its cookies, storage or session;
- `rmprofile`: the profile directory removed whole ("Forget all logins", an account deleted).

- the profile directory, created with mode 0700 by its uid (/profiles is 1733: anyone may create
  an entry, nobody may list it, only the owner may remove theirs);
- the Singleton* lock files removed: a lock left by another host name (a container recreated on
  the persisted volume) blocks Chromium for 20 s and more (B1). browserd runs no other Chromium
  when it calls this;
- the preferences Chromium reads at start: the languages (`--lang` is ignored on Linux, B1),
  continue where you left off (session cookies survive a restart), no password saving, no card
  autofill, downloads into the lease's directory without a prompt; in Local State, the remote
  debugging switch of chrome://inspect off and no saved chrome://flags entry;
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

LOCKS = ("SingletonLock", "SingletonSocket", "SingletonCookie")
PROFILE = re.compile(r"/profiles/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Where chrome://flags keeps its entries in Local State (the names in Chromium 155's binary).
FLAGS = ("enabled_labs_experiments", "enabled_labs_experiments_origin_lists")
# What Chromium rebuilds by itself: its disk caches (the cache lives in the profile, which is
# not under XDG_CONFIG_HOME), shader caches, crash reports. Cookies, Local Storage, IndexedDB,
# Session Storage, Sessions and Preferences are never in this list.
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
    off. The chrome://flags entries saved there keep applying once the page is blocked: they are
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
    else:
        sys.exit(f"unknown command {command}")
