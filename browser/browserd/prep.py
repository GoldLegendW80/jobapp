"""Run as the profile's own uid, never as root: `python -m browserd.prep prepare <profile dir>
<lease dir> <languages>`, `killpg <pgid>`, `kill <pid ...>`, `rmtree <lease dir>`. browserd is
root with SETUID and SETGID only (no DAC_OVERRIDE, no CHOWN, no KILL), so it cannot read or
write a profile nor signal its Chromium itself; this child does it under the profile's uid,
then exits.

- the profile directory, created with mode 0700 by its uid (/profiles is 1733: anyone may create
  an entry, nobody may list it, only the owner may remove theirs);
- the Singleton* lock files removed: a lock left by another host name (a container recreated on
  the persisted volume) blocks Chromium for 20 s and more (B1). browserd runs no other Chromium
  when it calls this;
- the preferences Chromium reads at start: the languages (`--lang` is ignored on Linux, B1),
  continue where you left off (session cookies survive a restart), no password saving, no card
  autofill, downloads into the lease's directory without a prompt;
- the lease's directory, mode 0700, for downloads.
"""

import contextlib
import json
import os
import shutil
import signal
import sys

LOCKS = ("SingletonLock", "SingletonSocket", "SingletonCookie")


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
    elif command == "rmtree" and args[0].startswith("/leases/"):
        shutil.rmtree(args[0], ignore_errors=True)
    else:
        sys.exit(f"unknown command {command}")
