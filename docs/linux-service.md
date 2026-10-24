# Run Research Runner on Linux with systemd

Use a **user service** under the same Linux account that owns Codex authentication,
sessions and workload tools. The example uses systemd's `%h` home-directory
specifier and the uv installation
from [Install and serve](../README.md#install-and-serve). Run the commands on
the server as that user; only enabling linger needs sudo.

## Check the installed environment

```sh
command -v research-runner
command -v codex
codex login status
```

The unit below assumes Runner and Codex are in `~/.local/bin`. Substitute
the actual absolute paths from these checks if your installation differs. If Codex
requires Node or workloads require other tools, include their
executable directories in the unit's PATH. systemd does not source `.bashrc`,
`.zshrc`, activate a venv, or expand shell variables such as `$PATH` in
`Environment=`. For a custom Codex home, replace `CODEX_HOME` with the existing
directory used by that account.

Create the service directories:

```sh
mkdir -p ~/.config/systemd/user ~/.config/research-runner
mkdir -p ~/.local/share/research-runner
chmod 700 ~/.config/research-runner ~/.local/share/research-runner
```

For an existing deployment, use its original absolute state path in `ExecStart`.
Keep its state and Codex home in place. Inspect the existing controller first;
stop it before starting this unit against the same state directory. Do not
delete its lock file or run a second controller against that directory.

## Configure the service

Save this as `~/.config/systemd/user/research-runner.service`, adjusting the paths:

```ini
[Unit]
Description=Research Runner
StartLimitIntervalSec=0

[Service]
Type=exec
WorkingDirectory=%h
Environment=HOME=%h
Environment=CODEX_HOME=%h/.codex
Environment=RESEARCH_RUNNER_CODEX_BIN=%h/.local/bin/codex
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=-%h/.config/research-runner/server.env
ExecStart=%h/.local/bin/research-runner --state %h/.local/share/research-runner serve --host 127.0.0.1
Restart=on-failure
RestartSec=10s
TimeoutStopSec=60s
UMask=0077

[Install]
WantedBy=default.target
```

The unit binds explicitly to localhost, including when using older release wheels
that defaulted to Tailscale discovery. For remote access, replace
`--host 127.0.0.1` with `--host SERVER_BIND_ADDRESS` in `ExecStart`, using an
address assigned to the server. Use a trusted private network or a configured
TLS reverse proxy; configure both tokens before enabling remote access.

Tailscale is optional. To discover its IPv4 address automatically, install and
connect Tailscale, ensure its executable directory is in the unit's PATH, and
replace `--host 127.0.0.1` with `--host tailscale` in `ExecStart`. Discovery failure stops startup and
systemd retries every ten seconds. The system Tailscale daemon must already be
enabled and authenticated; a user unit cannot order itself after a system service.

The optional environment file uses `KEY=value` lines, without `export` or shell
commands. To enable separate read and control roles, create
`~/.config/research-runner/server.env` containing two distinct private values:

```text
RESEARCH_RUNNER_READ_TOKEN=REPLACE_WITH_PRIVATE_READ_TOKEN
RESEARCH_RUNNER_CONTROL_TOKEN=REPLACE_WITH_DIFFERENT_PRIVATE_CONTROL_TOKEN
```

Replace the placeholders before starting and restrict the file to its owner:

```sh
chmod 600 ~/.config/research-runner/server.env
```

Set both tokens or neither. Without tokens, all callers on the trusted network
have controls. Keep the file outside Git. See the
[authentication guidance](../README.md#install-and-serve); do not expose Runner
publicly.

## Enable startup and inspect logs

After checking paths and configuration, enable linger so the user manager starts
at boot and remains running after SSH logout:

```sh
sudo loginctl enable-linger "$(id -un)"
systemd-analyze --user verify ~/.config/systemd/user/research-runner.service
systemctl --user daemon-reload
systemctl --user enable --now research-runner.service
systemctl --user status research-runner.service
journalctl --user -u research-runner.service -n 100 --no-pager
loginctl show-user "$(id -un)" -p Linger
```

The logs include the dashboard URL. Verify HTTP access on the server (use the
configured address instead if you enabled remote binding):

```sh
research-runner --url http://127.0.0.1:8765 capabilities
```

When tokens are enabled, set `RESEARCH_RUNNER_TOKEN` privately in the client
environment. Confirm the dashboard and capabilities before submitting work.

## Stop, restart and update

Inspect job status before maintenance: stopping or restarting interrupts active
work and does not automatically resume research.

```sh
systemctl --user stop research-runner.service
# Install the chosen tested wheel with its server extra while the service is stopped.
# Use uv tool install --force --python 3.12 '...whl[server]' to replace the installation.
systemctl --user start research-runner.service
systemctl --user status research-runner.service
```

After editing the unit, run `systemctl --user daemon-reload` before restarting.
For an unchanged installation, use `systemctl --user restart research-runner.service`.
Keep the same state directory, user, Codex home and runtime when resuming jobs.
An older wheel can be reinstalled the same way, but do not assume state written
by a newer version is compatible with it. Follow the
[snapshot guidance](../README.md#recovery-and-snapshots) before an upgrade.

References: [uv tools](https://docs.astral.sh/uv/guides/tools/),
[systemd service settings](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html),
[loginctl linger](https://www.freedesktop.org/software/systemd/man/latest/loginctl.html).
