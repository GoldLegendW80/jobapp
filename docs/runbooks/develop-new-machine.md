# Develop on a new machine

Set up a laptop to build the app: the repository docs, tools, pre-commit, the TLS inspecting proxy case.

Everything the build needs is in this repository: [CLAUDE.md](../../CLAUDE.md) is where Claude Code
starts (the map, the hard rules, the daily commands), `docs/agents/` holds the agents' rules and
the workflow, [docs/STATUS.md](../../docs/STATUS.md) the state and [docs/decisions.md](../../docs/decisions.md)
the owner's decisions. Nothing depends on another machine's temporary files.

```bash
gh auth login -h github.com -s project,workflow    # repo is in the default scopes; project for the board, workflow to deploy and operate (and push .github/workflows)
gh repo clone GoldLegendW80/jobapp && cd jobapp
brew install uv                                    # or curl -LsSf https://astral.sh/uv/install.sh | sh
npm install -g pnpm@9.15.9                         # the pnpm of packageManager in web/package.json (pnpm 10 or newer runs it itself, no corepack needed); Node 22.12 or newer
uv tool install pre-commit && pre-commit install   # the hooks, secret scan included, once per clone
pnpm --dir web install && (cd api && uv sync)      # dependencies (add --native-tls to uv on a network that intercepts TLS)
ops/dev-setup.sh                                   # read only: lists what is still missing and the command that fixes it
```

- **Docker Desktop** running, with at least 16 GiB free on the disk (the merge gate refuses to
  start under 12).
- **The Jetson needs no ssh from a laptop**: deploys and operations go through GitHub
  (deploy-from-any-machine.md). A gh account with write access to the repository and the scope `workflow` is all it
  takes. Only the owner's own steps on the Jetson (sudo, a restore, an admin link) still use ssh;
  `ops/dev-setup.sh --jetson` checks that `ssh jetson true` answers without a prompt.
- **Claude Code**: install it (`npm install -g @anthropic-ai/claude-code`) and run `claude` from
  the repository root. It loads `CLAUDE.md` and the versioned `.claude/` config: the
  `implementer` and `reviewer` subagents, the commands `/build-task`, `/review-pr`, `/fix-pr`,
  `/ship` and `/status`, and `settings.json`, whose permissions allow the common read only and
  test commands, ask before a merge, a gate, a deploy or ssh, and deny reading `.env` files.
  Personal settings go in `.claude/settings.local.json` (git ignored); agent worktrees live in
  `.claude/worktrees/` (git ignored).
  If an older clone has the line `.claude/` in `.git/info/exclude`, narrow it to
  `.claude/worktrees/`: left as it is, it hides the versioned `.claude/` config from git.

**Behind a TLS inspecting proxy.** On a network that answers for every site with a certificate of
its own, the Mac trusts that certificate and the containers of an image build do not: every build
stops where it downloads (uv says `invalid peer certificate: UnknownIssuer`). Export the
certificates the Mac trusts to a file outside the repository, and name it in `JOBAPP_EXTRA_CA`:

```bash
mkdir -p ~/.config/jobapp
( umask 077
  { security find-certificate -a -p /Library/Keychains/System.keychain
    security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain
  } >~/.config/jobapp/extra-ca.pem )
export JOBAPP_EXTRA_CA="$HOME/.config/jobapp/extra-ca.pem"   # an absolute path; add it to ~/.zshrc to keep it
docker compose -p jobapp-x -f compose.yml -f compose.mac.yml -f compose.extra-ca.yml build   # your own builds: add the file last
```

`ops/local-check.sh` adds `compose.extra-ca.yml` to its image builds by itself when the variable
is set, and refuses a path that is relative or not a readable file. The bundle reaches only the
build steps that download (uv, pip, pnpm, the Playwright download), as a BuildKit secret: it is in
no image layer and in no container (a search of every layer of the five images found none of it,
2026-10-15, issue #359). `tests/browser_egress_test.sh`, which installs iptables in a throwaway
Alpine container, does the same: with the variable set, it installs them in a throwaway image
build that alone gets the bundle as a BuildKit secret, then fails if a line planted in the bundle
shows in that image's files, variables, labels or history.
Unset, the builds are the Jetson's own: `deploy.sh` and the deploy workflow build with
`compose.yml` alone (`tests/extra_ca_test.sh`). The file names the inspecting certificate of your
network: keep it out of the repository and out of pull requests.
