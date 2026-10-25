# Reboot and crash recovery

What comes back by itself after a reboot or crash and what to check.

Every service but the browser has `restart: unless-stopped` and `docker.service` is enabled at boot, so the daemon
starts the stack again after a reboot; `tailscale` logs in again from its saved state, with the same URL.
`tests/crash_check.sh` proves the restart half without sudo: it kills each container's main process
with SIGKILL from the host PID namespace (what the OOM killer or a segfault looks like) and waits
for the daemon to restart it healthy. `docker kill` and `docker compose kill` count as a manual
stop, which `unless-stopped` deliberately leaves down. The one exception is the browser (profile
`browser`): it has `restart: "no"`, so after a reboot it stays down until the next deploy, which checks
its host firewall before starting it (`ops/browser-egress.sh`). The reboot itself needs sudo, so it is run by
hand:

```bash
tests/reboot_check.sh 2>&1 | tee tasks/evidence/task9/reboot.log   # asks for the sudo password once
```

It reboots the Jetson, waits for ssh with a new boot id, waits for the five services to be healthy,
runs `./deploy.sh smoke` and prints the new URL and the seconds since boot; it passes when the
stack is healthy within 120 seconds of boot.
