"""A stand in for sandbox/main.py, for the tests that run the server in the test process: it
takes the same command line (kind, options as JSON), the bytes on standard input, and does what
`options["act"]` says. No lockdown here: those tests are about the server, and run on any
system. The real child is tested in the container (test_container.py)."""

import json
import os
import signal
import sys
import time


def main() -> int:
    started = time.time()
    # The server appends the kind and the options to the command it was given: anything before
    # them is this stand in's own (`--answer FILE`: answer with the bytes of that file).
    *own, kind, raw_options = sys.argv[1:]
    options = json.loads(raw_options)
    if own[:1] == ["--answer"]:
        sys.stdin.buffer.read()
        with open(own[1], "rb") as handle:
            sys.stdout.buffer.write(handle.read())
        return 0
    act = options.get("act", "echo")
    if act == "no_read":  # ends without reading its input: the server's write meets a closed pipe
        return int(options.get("status", 0))
    data = sys.stdin.buffer.read()
    if act == "sleep":
        time.sleep(float(options["s"]))
    elif act == "flood":
        block = b"x" * 65536
        for _ in range(int(options["blocks"])):
            sys.stdout.buffer.write(block)
        sys.stdout.buffer.flush()
        return 0
    elif act == "exit":
        return int(options["status"])
    elif act == "signal":
        os.kill(os.getpid(), getattr(signal, options["name"]))
        time.sleep(5)
    elif act == "raw":  # write these bytes as the answer, whatever they are
        sys.stdout.buffer.write(options["text"].encode("utf-8", "surrogatepass"))
        sys.stdout.buffer.flush()
        return 0
    elif act == "close_then_sleep":  # closes its output and stays: only the wall clock ends it
        os.close(1)
        time.sleep(float(options["s"]))
        return 0
    elif act == "touch":  # leaves a trace: the test can tell that this child ran
        with open(options["path"], "w", encoding="ascii") as handle:
            handle.write("ran")
    answer = {
        "ok": True,
        "kind": kind,
        "received": len(data),
        "options": options,
        "environ": sorted(os.environ),
        "started": started,
        "ended": time.time(),
    }
    sys.stdout.buffer.write(json.dumps(answer).encode())
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
