"""D1 spike, throwaway: the container's memory, sampled every 10 ms while a measurement runs.

cgroup v2 seen from inside the container (the Jetson's kernel 5.15 has no memory.peak, so the
peak is sampled): `current` is what mem_limit counts (page cache included), `anon` the private
memory of the processes, `pids` the processes and threads that pids_limit counts, `rss` the sum
of VmRSS of every process this uid may read (shared libraries counted once per process, so it
overstates a browser's several processes).
"""

import os
import threading
import time

CGROUP = "/sys/fs/cgroup"
MIB = 1024 * 1024


def _read(name: str) -> str:
    try:
        with open(os.path.join(CGROUP, name)) as f:
            return f.read()
    except OSError:
        return ""


def sample() -> dict[str, int]:
    current = _read("memory.current").strip()
    anon = 0
    for line in _read("memory.stat").splitlines():
        key, _, value = line.partition(" ")
        if key == "anon":
            anon = int(value)
    rss = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss += int(line.split()[1]) * 1024
        except OSError:
            continue
    pids = _read("pids.current").strip()
    return {
        "current": int(current or 0),
        "anon": anon,
        "rss": rss,
        "pids": int(pids or 0),
    }


class Watch:
    """with Watch() as w: ...; w.peak_mib -> {"current", "anon", "rss"} in MiB and "pids"."""

    def __enter__(self):
        self.peak = sample()
        self.base = dict(self.peak)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            for key, value in sample().items():
                self.peak[key] = max(self.peak[key], value)
            time.sleep(0.01)

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        for key, value in sample().items():
            self.peak[key] = max(self.peak[key], value)

    @property
    def peak_mib(self) -> dict[str, int]:
        return {
            key: value if key == "pids" else round(value / MIB)
            for key, value in self.peak.items()
        }

    @property
    def base_mib(self) -> dict[str, int]:
        return {
            key: value if key == "pids" else round(value / MIB)
            for key, value in self.base.items()
        }
