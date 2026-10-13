"""What the smoke scripts ask about the `parser` service (ingestion G2a), in one place so that
tests/compose_smoke.sh, tests/jetson_smoke.sh and tests/ingestion_parser_jetson.sh judge it
alike. Standard library only, and nothing newer than Python 3.8: it also runs on the Jetson.

    docker inspect <container> | python3 tests/parser_check.py inspect [options]
        Judge the container as it runs: uid, capabilities, read only root, no new privileges,
        pids and memory limits, ipc, its one network, nothing published, nothing mounted, and
        an environment that holds no secret. Prints one line, or the problems and exits 1.
          --secret-name NAME   a variable that must not be in its environment (repeat it for
                               every key of the stack's .env: names only ever travel)
          --code-mount         allow the application's code mounted read only (the dev stack)

    <compose> exec -T <service> python -I - reach HOST:PORT... < tests/parser_check.py
        From inside a container: resolve each host, then connect. One line per target,
        `HOST:PORT resolved=0|1 connected=0|1`, exit 0. The caller decides what must fail.

It prints names, numbers and yes or no. Never a value of an environment variable.
"""

import json
import socket
import sys

MIB = 1024 * 1024
UID = "10002:10002"
MEMORY = 640 * MIB
PIDS = 32
NETWORK_SUFFIX = "_parsing"
CODE_MOUNT = "/app/app"
# Words that name a secret (app/worker.py drops the same from the worker's environment).
SECRET_WORDS = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "AUTH",
    "DSN",
    "DATABASE",
    "REDIS",
)
# GPG_KEY is set by the official python image: the fingerprint of the key that signs Python's
# releases, public by nature.
NOT_SECRET = ("GPG_KEY",)


def problems_of(container, secret_names=(), code_mount=False):
    """Why this `docker inspect` object is not the parser as compose.yml hardens it."""
    host, config, state = (
        container["HostConfig"],
        container["Config"],
        container["State"],
    )
    found = []

    def expect(what, got, wanted):
        if got != wanted:
            found.append(f"{what} is {got!r}, expected {wanted!r}")

    expect("user", config.get("User"), UID)
    expect("cap_drop", host.get("CapDrop"), ["ALL"])
    expect("cap_add", host.get("CapAdd") or [], [])
    expect("privileged", host.get("Privileged"), False)
    expect("read only root", host.get("ReadonlyRootfs"), True)
    expect("security_opt", host.get("SecurityOpt"), ["no-new-privileges:true"])
    expect("pids limit", host.get("PidsLimit"), PIDS)
    expect("memory limit", host.get("Memory"), MEMORY)
    expect("ipc mode", host.get("IpcMode"), "none")
    expect("pid mode", host.get("PidMode"), "")
    expect("userns mode", host.get("UsernsMode") or "", "")
    expect("tmpfs", host.get("Tmpfs") or {}, {})
    expect("devices", host.get("Devices") or [], [])
    expect("port bindings", host.get("PortBindings") or {}, {})

    networks = sorted(container["NetworkSettings"]["Networks"])
    if len(networks) != 1 or not networks[0].endswith(NETWORK_SUFFIX):
        found.append(f"networks are {networks!r}, expected the parsing network alone")
    published = [
        port
        for port, bound in (container["NetworkSettings"].get("Ports") or {}).items()
        if bound
    ]
    expect("published ports", published, [])

    for mount in container.get("Mounts") or []:
        target = mount.get("Destination")
        if not (code_mount and target == CODE_MOUNT and mount.get("RW") is False):
            found.append("mount at {} (writable: {})".format(target, mount.get("RW")))

    names = sorted(entry.partition("=")[0] for entry in config.get("Env") or [])
    if "PARSER_PORT" not in names:
        found.append("PARSER_PORT is not in its environment")
    for name in names:
        if name in secret_names:
            found.append(f"{name} of the stack's .env is in its environment")
        elif name not in NOT_SECRET and any(
            word in name.upper() for word in SECRET_WORDS
        ):
            found.append(f"{name} in its environment is named like a secret")
        elif "armcap" in name.lower():
            found.append(
                f"{name} in its environment: the parser never imports cryptography"
            )

    expect("running", state.get("Running"), True)
    expect("health", (state.get("Health") or {}).get("Status"), "healthy")
    expect("killed for memory", state.get("OOMKilled"), False)
    return found


def inspect(arguments):
    secret_names, code_mount = [], False
    while arguments:
        option = arguments.pop(0)
        if option == "--secret-name" and arguments:
            secret_names.append(arguments.pop(0))
        elif option == "--code-mount":
            code_mount = True
        else:
            print(f"parser_check: unknown option {option}", file=sys.stderr)
            return 2
    try:
        containers = json.load(sys.stdin)
    except ValueError:
        print(
            "parser_check: standard input is not the JSON of docker inspect",
            file=sys.stderr,
        )
        return 2
    if not isinstance(containers, list) or len(containers) != 1:
        print("parser_check: expected one container", file=sys.stderr)
        return 2
    container = containers[0]
    found = problems_of(container, secret_names, code_mount)
    for problem in found:
        print(f"parser: {problem}", file=sys.stderr)
    if found:
        return 1
    names = sorted(
        entry.partition("=")[0] for entry in container["Config"].get("Env") or []
    )
    host = container["HostConfig"]
    network = min(container["NetworkSettings"]["Networks"])
    mounted = " but the code, read only" if container.get("Mounts") else ""
    print(
        f"uid {container['Config']['User']}, no capability, no new privileges, "
        f"read only root, ipc none, {host['PidsLimit']} pids, "
        f"{host['Memory'] // MIB} MiB, network {network} alone, "
        f"nothing published or mounted{mounted}, {len(names)} variables and none "
        f"of the {len(secret_names)} names of .env, "
        f"restarts {container.get('RestartCount', 0)}"
    )
    return 0


def reach(targets):
    for target in targets:
        host, _, port = target.rpartition(":")
        resolved = connected = 0
        try:
            address = socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)[0][
                4
            ][0]
            resolved = 1
            connection = socket.create_connection((address, int(port)), timeout=4)
            connection.close()
            connected = 1
        except OSError:
            pass
        print(f"{target} resolved={resolved} connected={connected}")
    return 0


def main(argv):
    if len(argv) > 1 and argv[1] == "inspect":
        return inspect(list(argv[2:]))
    if len(argv) > 2 and argv[1] == "reach":
        return reach(argv[2:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
