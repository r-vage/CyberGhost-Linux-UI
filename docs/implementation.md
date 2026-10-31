# Implementation and validation

Created 2026-10-30 as an independent project, now named `CyberGhost-Linux-UI`.
No Nova or ComfyUI dependencies. Application IDs, launchers, preferences and the
installed startup/firewall ownership markers retain their original identifiers
so the repository rename does not strand existing configuration.

## Boundaries

The application is a normal-user GTK 3 process. `cyberghost_desktop/cli.py` builds
argument arrays for the installed `cyberghostvpn` executable, never shell command
strings. Connect/disconnect and explicit kill-switch changes use
`pkexec --disable-internal-agent`.
After authorization, `/usr/bin/env` sets `SUDO_USER` from the invoking process's
real UID and system account record. CyberGhost CLI 1.5.0 uses that name to locate
its existing configuration; without it, pkexec's root identity points at
`/home/root/.cyberghost/config.ini`. Only this identity and the C locale are added
to pkexec's sanitized environment. The app does not copy or read VPN credentials.
No sudoers rule or stored password is installed. The separate optional authorization-retention setting installs an app-specific polkit policy.
Enabling the optional kill switch starts a small root status helper for that session.
Enabling the separate startup option installs an explicitly authorized boot service.
The command preview is display-only and is not executed through a shell.

The UI uses background workers, separate status and catalog requests, bounded
read timeouts, and request tokens. Old country/server/status results cannot replace
newer selections or a later connection action. Unstarted obsolete requests are
cancelled. Errors are visible, and the CLI's result is available in Activity.
Location requests wait for startup inspection and any running boot connection.
Known DNS/connection exceptions and catalog timeouts retry every three seconds
within a three-minute window, without blocking status checks or repeating VPN
commands. Temporary failures show one waiting message; successful recovery loads
the list automatically. Permanent account/configuration errors appear immediately,
and exhausted network retries expose the error and CLI details. Refresh, category
changes and window destruction cancel pending retry timers; request tokens and
selection epochs discard obsolete in-flight results.
Connection status comes from `--status` and read-only Linux interface/routing
checks, never from a successful connect command, a selected location, or a cached
preference. CLI 1.5.0 runs `wg show` internally and incorrectly reports no VPN when
the normal user cannot read WireGuard's privileged peer details. If the CLI does
not report a connection, the app queries `ip -j -details link show type wireguard`
and checks the exact `cyberghost` interface's UP flag. WireGuard's usual UNKNOWN
operstate is not treated as a failure. An IPv4 `ip route get 1.1.1.1` lookup must
also resolve through that interface; this only reads the kernel's routing tables
and does not send traffic to that address. Other WireGuard interfaces are ignored.
Missing tools, permission failures, timeouts or incomplete routing produce unknown
status, which disables Connect while keeping Disconnect available. Connected
describes the detected tunnel/routing state, not an independent handshake or leak test.

Changing selection or closing the app does not change the VPN. The UI blocks
quitting during a connection action so it does not kill a privileged command
halfway through network setup. Polkit handles cancellation of authorization.
Catalog reads can finish for up to 40 seconds after exit; queued reads are cancelled.

Preferences contain the interface language, mode/protocol/transport, last location,
favorites and cached location catalogs.
They use an atomic private file under the user's config directory. Runtime CLI
output is not persisted. The separate demo client neither invokes subprocesses
nor reads/writes real preferences.

## Interface languages

The header selector loads 15 interface languages from packaged UTF-8 JSON catalogs
in `cyberghost_desktop/locales`, overlaid with personal catalogs in
`$XDG_CONFIG_HOME/cyberghost-desktop/locales` (default `~/.config`). Additional
catalogs in either folder are discovered at launch without a Python registry.
The loader checks metadata and named format placeholders. Invalid personal entries
fall back to the bundled language, then English; malformed files are skipped.
An unavailable saved language also falls back to English.

`locale_store.py` creates and merges personal copies on normal startup. The
packaged `locale_manifest.json` contains schema/app versions and SHA-256 hashes
of shipped files. The personal `.manifest.json` retains prior defaults and their
hashes, observed source hashes and conflict keys. Three-way merging works per
message: new keys are added, unchanged defaults follow upstream, and customized
values survive, including on conflicts. Removed default keys are deleted only
when unmodified. Unknown baselines preserve existing wording conservatively.
Personal-only and incomplete languages receive missing English keys. A changed
bundled file is treated as user work, rather than becoming a trusted baseline.

Atomic writes preserve exact customized originals in content-addressed `.backups`
before rewriting; `.upstream` holds the latest trusted defaults for conflict
review. Invalid personal JSON is left untouched. Failed backups prevent rewriting
the affected catalog and report a load error in Activity. Demo and nonpersistent
preferences skip synchronization; demo also skips personal catalogs entirely.

`UIStrings` retains the source message for each display property and updates those
properties in place. Combo rows keep their original CLI IDs. A language change
does not rebuild the window, refresh the catalogs, issue VPN commands or reset
connection/protection state. The saved language uses the existing atomic
preferences file; demo preferences remain in memory. Raw CLI/system diagnostics
and remote location identifiers are preserved. Privileged helpers do not load
translation files.

`tools/check_translations.py` validates catalog structure, duplicates, source keys,
placeholder compatibility, coverage and manifest freshness. `--write-manifest`
regenerates the packaged manifest for a translation PR or version change.
`tests/test_locale_store.py` checks per-message merging, new personal languages,
conflicts, backups, unknown baselines and damaged files. `tests/test_i18n.py` checks discovery,
fallback, malformed input, nested messages and preferences. `tests/gui_i18n.py`
uses demo backends to check live GTK updates, Setup, selected-server preservation,
unchanged commands and addresses, saved-language restoration and binding cleanup.
It switches through every discovered catalog, including right-to-left text.
`tests/gui_locale_store.py` checks normal startup with temporary personal files,
automatic discovery, new keys and malformed-file diagnostics using fake backends.
See the [translation contributor guide](TRANSLATING.md).

## App updates and restart

The header displays `cyberghost_desktop.__version__`. **About and updates** shows
that version and offers a manual **Check for updates** button. The dialog uses
the existing worker pool, so a pending request does not block GTK. Repeated clicks
are ignored while checking, failures can be retried, and callbacks for closed
dialogs or windows are discarded. Every result is bound to the current UI language.

The checker first resolves `refs/heads/main` through GitHub's
[Get a reference API](https://docs.github.com/en/rest/git/refs#get-a-reference),
using a unique query parameter and no-cache headers on each explicit check.
It validates the branch name, object type and hexadecimal commit ID, then reads
the literal `__version__` assignment in `cyberghost_desktop/__init__.py` through
that exact commit's raw URL. Response-supplied URLs are never followed.
The API is public and requires neither Git nor a token; rate limits and other
HTTP failures are reported as check failures without falling back to stale data.

This fixes a reproduced caching issue: after commit `b754a88` was pushed, Git and
the branch API returned that commit with version `0.1.4`, while the raw `main`
URL still returned `0.1.3` with `Cache-Control: max-age=300` and `X-Cache: HIT`.
Adding a query parameter to the raw branch URL did not bypass that response.
Reading the file by commit returned the correct version immediately.

This matches the repository's versioned-commit workflow and works without GitHub
release entries. Only stable `major.minor.patch` strings are accepted; numeric
comparison handles multi-digit components and distinguishes a newer local
development version from an available update. The file is read as text and never
imported or executed. Update links use a fixed repository URL.

Both HTTPS requests verify TLS, each has an eight-second socket timeout and reads at most
8 KiB plus one byte to detect oversized responses, and both refuse redirects and ignore
proxy environment variables. They use the current network route and are subject to
VPN-only firewall rules. There is no startup polling or automatic installation.
Demo mode disables checks and installation. ZIP installations use the manual
update instructions.

**Install update** uses `self_update.py` in the worker pool. It requires a Git
checkout at the repository root, the official origin and branch `main`, no pending
Git operation, and no local commits or changes other than valid unstaged/untracked
language JSON files. It fetches origin/main, checks the downloaded version and
restart script, verifies fast-forward ancestry, then rechecks HEAD and local edits.
Only then does it merge with `--ff-only --no-autostash --no-overwrite-ignore`.
This separates the fetch and integration phases of
[`git pull --ff-only`](https://git-scm.com/docs/git-pull) to validate before
changing working files. Git hooks and interactive credential prompts are disabled;
subprocesses have timeouts. There is no stash, reset, merge commit or force update.

Before restoring modified bundled language files, the updater backs up every
original and adopts its edits into the personal store using the committed file
as the old baseline. It rechecks for concurrent edits before restoring defaults.
Untracked language files are removed from the checkout only after preservation.
The next startup merges the newly installed defaults and new feature keys into
these personal files. Other source work, staged files and local commits require
manual updates. Backup failures leave language source files in place.

During installation, VPN/setup/startup mutations and quitting are blocked. After
success, VPN controls remain paused until **Restart app** loads the new code.
The running version stays visible until then. A failure that may have partially
changed the checkout also requires reopening the app; files are never reset to
hide a failed update. `__main__.py` restarts through this checkout's `run.sh`,
preserving `--no-tray` and dropping the one-time `--setup` flag. The VPN and firewall
are independent of the GUI process and remain active. Protected helper copies
still require their explicit Setup/Startup update actions after restarting.

`tests/test_updates.py` covers version comparison, stale branch responses, fresh
commit resolution, literal parsing, malformed and oversized metadata, network
failures, timeouts and redirects. `tests/test_self_update.py`
uses temporary local Git repositories for updates, local work preservation,
translation migration, concurrent edits, backup failures and failed checkouts.
`tests/test_restart.py` checks restart arguments and errors. `tests/gui_updates.py`
checks manual requests, all 15 languages, retry, duplicate clicks, demo isolation,
install/quit interlocks and restart with a simulated active VPN and protection.

## Public IP and layout

The status panel queries `https://1.1.1.1/cdn-cgi/trace` on connection-state changes
or Refresh. TLS verification is enabled, HTTP proxies and redirects are disabled,
the response is bounded to 16 KiB, and requests time out after six seconds. Only
a validated public IPv4 address and optional two-letter country code are retained.
The address from before connecting is captured only while disconnected. Opening
the app with an existing VPN does not invent a baseline. IP results are never
logged or persisted, and request tokens discard callbacks from an older connection.
Failures leave VPN status unchanged. With VPN only enabled and the VPN disconnected,
the UI reports blocked access without attempting the public lookup. Demo and GUI
tests use documentation-range addresses and make no external IP requests.

The main layout uses a horizontal GTK Paned: the left pane does not resize with
the window, while the right pane does. The divider remains adjustable. Combo box
text ellipsizes instead of forcing large minimum widths for long server names.

## Optional WireGuard kill switch

The normal-user client calls `/usr/bin/python3 -I firewall.py enable|disable`
through pkexec only on an explicit toggle. The isolated helper uses standard
library imports, fixed action names and tools found in trusted system directories.
There is no general command runner, custom polkit policy or automatic activation.
The helper validates CyberGhost's exact WireGuard interface, UP flag, default
IPv4 route and current nonzero firewall mark before enabling. It never reads
WireGuard private keys or the CyberGhost account file.

One atomic nft transaction creates/replaces the app-owned `inet cyberghost_desktop`
table. A matching ownership comment is required before replacing/deleting an
existing table. Other applications' rules and wg-quick's own tables are unchanged.
Output and forward chains drop traffic by default. Allowed paths are the exact
`cyberghost` interface, loopback, WireGuard's marked UDP outer transport, DHCP and
IPv6 neighbor discovery. Forwarded return traffic from the tunnel must be established
or related. Established direct output connections are deliberately not exempted.
Both IP versions are covered; disappearing/recreated interfaces are matched by name.

After activation a root helper serves a Unix socket in a root-owned runtime
directory. Normal users may request status; mutations require a root peer, reached
through another polkit prompt. The client also checks that its server is root.
Status compares the actual table against its verified rule fingerprint, ignoring
only kernel-assigned handles. An activation marker is written before mutation:
if the helper crashes, the UI reports unknown and offers authorized removal rather
than assuming off. Disable works without an active tunnel or running helper.
The monitor exits on disable; reboot removes the transient rules/runtime data.
No setup files or firewall configuration are installed persistently.

The UI never turns protection off on disconnect, close or reconnect. A protected
WireGuard Connect uses one polkit-authorized helper. It creates a private root-owned
cgroup v2 for the CLI and inserts a temporary output exception matching only that
cgroup's root sockets. Existing output/forward drop policies stay in place. The
helper serializes with other firewall changes, verifies the new interface and
route, refreshes the WireGuard transport mark, and removes its exception on success
or failure. Cleanup restores the previous rule fingerprint after a failed attempt.

The CLI gets a private mount namespace with its configured uplink DNS servers and
`hosts: files dns`, preventing system-resolver requests from other applications from
escaping through the exception. Any shared nscd socket is hidden in that namespace.
WireGuard's resolvconf/resolvectl updates re-enter the original mount namespace, so
the tunnel still configures the host's DNS. No global resolver file is replaced.
Unavailable cgroup/namespace/DNS support fails without disabling protection.
`tests/reconnect_namespace.py` verifies the resolver isolation and DNS updater
forwarding with inert executables in fresh user, network and mount namespaces.
`tests/firewall_namespace.py` checks the real nftables cgroup match, IPv4/IPv6
blocking and restoration of the original rule fingerprint.
The reconnect helper supports the existing root monitor's enable/status protocol;
the monitor does not need to be stopped or upgraded just to reconnect. Catalogue
requests use saved locations and in-memory city/server choices while disconnected;
fresh lists can be requested after the new tunnel is up. OpenVPN is outside this
option's supported scope.
Existing firewall managers can impose additional restrictions; a root administrator
can also change/remove these rules. This is not a boot-time or privileged-process
containment mechanism.

## Optional boot connection

Startup is off by default. Enabling it calls `startup_system.py configure` through
pkexec once, with a validated selection and optional VPN-only flag. The account
comes from pkexec's authenticated `PKEXEC_UID` and the system password database,
not a supplied username. The helper verifies that the CLI and its ancestor
directories are root-owned and not writable by other users. Its existing account
file must be present; credentials are not read or copied by setup.

The helper installs copies of `startup_system.py`, `cli.py` and `firewall.py` under
`/usr/local/libexec/cyberghost-desktop/startup-UID/`, a root-owned data-only profile
at `/etc/cyberghost-desktop/startup-UID.json`, and the unit
`cyberghost-desktop-startup-UID.service`. Runtime execution uses isolated Python
and these protected copies. It never loads code from the checkout or user preferences
at boot. Only one account may configure the machine's boot connection; setup
operations are serialized. Unrelated files are rejected and failed updates restore
the previous owned files. Changes require authorization; no general command runner
or passwordless polkit/sudoers exception is created.

The service is enabled for `multi-user.target`, ordered after `network-online.target`.
It uses `Type=exec`, `RemainAfterExit=yes` and `Restart=no`. Setup never uses `--now`
or starts/stops the VPN. The runner preserves an existing connection, waits up to
three minutes for an IPv4 route, and calls the official CLI once with a fixed argument
array and the account's `SUDO_USER`. It does not terminate a connection command
midway through network changes. Status is checked afterward; WireGuard additionally
requires the real tunnel and route. Optional VPN-only protection is enabled and
verified only after connection. Existing firewall rules are never silently disabled
to make a new connection possible.

The user-level XDG autostart entry opens the UI after desktop login; it does not
connect or retrigger the service. The entry is staged before authorization, so
cancellation does not activate it. The UI reads the root-owned profile, service
MainPID/Job/ActiveState and a small runtime status file. Both executing and queued
boot jobs block overlapping manual Connect/Disconnect/setup actions. Unverifiable
startup state stays refreshable. Closing the UI does not interrupt the independent
boot service. Runtime status contains progress/error messages, not credentials,
public IP addresses or raw CLI output.

Disabling startup disables the unit without stopping it, removes owned setup files
and the desktop autostart entry, and leaves any live VPN/firewall child processes
alone. The current boot unit can remain active until shutdown; it is not scheduled
on later boots. The normal-user uninstaller asks users to disable startup first.
Moving the checkout and rerunning the installer updates the desktop autostart path.
Updating startup's root-owned helper copies requires saving startup settings again.

Before-login connection requires the account file to be accessible during boot.
Startup does not block all early boot traffic and is not an automatic reconnect
daemon. The interface cannot record a before-VPN public address if boot connection
has already completed before it opens.

## CLI setup

The UI can start before CyberGhost is installed. Setup checks executable paths
and configuration-file metadata without reading credentials. It opens automatically
for a missing CLI, configuration or pkexec; the header button makes it available
later. Check again redetects a newly installed CLI in the running application.

Downloads remain in the user's browser and CyberGhost account. After the user
selects the official download's extracted `install.sh`, an unprivileged terminal
helper runs `sudo -- /bin/bash -- <installer>` from that installer directory.
Account configuration runs `sudo -- <cyberghostvpn> --setup`. These processes
inherit the terminal's input/output; the GUI does not capture or log credentials.
Supported terminal launchers wait until the helper exits. Connection actions and
closing the app are blocked while interactive setup runs, and the helper keeps
the terminal open so users can read failures before acknowledging them.

Only explicit Setup actions run installer/account commands. Opening Setup, checking
status and demo mode do not install software. The local CyberGhost installation
was not modified to test this feature: tests use a harmless installer and a fake
sudo executable, plus simulated installation state in the real GTK dialog.

## Commands checked against the installed CLI

Installed version: CyberGhost CLI **1.5.0**. Its local `--help` lists `--connection`
for OpenVPN UDP/TCP, which this app uses. Some help articles show different aliases;
the installed CLI's argument contract takes precedence.

Read-only commands verified on this system:

```text
cyberghostvpn --help
cyberghostvpn --status
cyberghostvpn --traffic --country-code
cyberghostvpn --torrent --country-code
cyberghostvpn --streaming --country-code
cyberghostvpn --traffic --country-code DE --city
cyberghostvpn --traffic --country-code DE --city Frankfurt --server
```

The production client/parser returned 100 general locations, 239 distinct streaming
services, 86 P2P locations, 3 German cities and 993 Frankfurt servers on 2026-10-30.
These counts and loads change; no catalog is hardcoded in the real client.

Connection commands are constructed as documented by CyberGhost and tested with
injected process boundaries. The actual machine's VPN was **not connected or
disconnected** during development. Real privileged tunnel establishment remains
to be checked when the user chooses Connect. Unknown future CLI status output is
reported as unknown, not as an active connection.

The WireGuard status fix was also checked against the user's already active
connection as UID 1000: the CLI reported no VPN and `wg show` returned permission
denied, while the production client correctly detected the UP WireGuard interface
and its active IPv4 route. No connection/disconnection was performed for that check.

## Checks

- Standard-library unit tests: parsers, command construction, no shell use,
  invoking-account preservation through simulated polkit environment sanitization,
  authorization cancellation, timeouts, demo isolation, private preferences,
  launcher installation/removal, ZIP scripts without executable permissions, moved
  checkouts and refusal to overwrite unrelated files; setup detection, literal
  installer paths, working-directory handling, terminal fallback, credential
  stream isolation and setup failures; WireGuard false negatives, interface
  removal, other VPN interfaces, routing failures, missing tools, malformed
  responses and permission/time-out errors; bounded IP lookup, redirect/proxy
  handling, invalid responses, firewall rule validation, actual-state verification,
  scoped updates, activation preconditions, fixed privileged actions and cancellation.
- Startup checks cover protected-copy installation, enable without start, removal
  without stop, failed-update rollback, untrusted paths, payload validation, desktop
  entry staging/cancellation/relocation and a simulated boot runner with the real
  CLI adapter: correct account, offline startup, missing account file, existing VPN,
  route verification, connection failure and post-connect firewall checks.
- GTK tests run on a separate Broadway display with the demo backend: location
  selection, favorites, search, protocol/transport, city/server, streaming,
  Connect/Disconnect, authorization failure, stale async callbacks and controls
  for unknown/connected WireGuard status; fixed left width on resize, long labels,
  public IP baseline/exit, stale IP callbacks, lookup failure, kill-switch state
  across disconnection and cancelled firewall authorization.
- GTK startup checks cover enable/update/disable, no immediate connection, boot-job
  control locking, unknown-state recovery and cancelled setup authorization.
- Login timing regressions use the actual GTK window with controlled futures:
  delayed service inspection, running boot jobs, repeated DNS failures, automatic
  location recovery, bounded retries, immediate account errors, stale results
  after a category change and cancellation on close. CLI tests distinguish
  network failures (including zero-exit tracebacks) from account/certificate errors
  and prevent marking privileged connection commands for automatic retries.
- Separate GTK setup checks cover automatic first launch, new CLI detection,
  controls during interactive setup, failure recovery and already installed CLI state.
- Linux compatibility checks cover distro/ID_LIKE detection, package-manager
  priority, older yum-based systems, unknown distributions, missing Bash,
  Ptyxis argument handling and a systemctl binary without running systemd.
  Non-systemd startup refuses authorization and leaves manual controls usable.
- Kernel firewall tests run inside new user/network namespaces, with a second
  isolated network namespace as a forwarding client. They test IPv4 and IPv6,
  existing direct sockets, marked versus unmarked UDP, loopback, forwarded traffic,
  tunnel removal, modified-rule detection and preservation of another firewall
  table. The actual host firewall and active VPN were not changed.
- The README screenshots and five-scene [visual tour](Visual_Tour.md) are
  rendered from actual GTK widgets using in-memory demo data. Setup uses
  simulated missing-installation state. The capture script waits for stable
  layout and checks that callout targets are visible; no live connection is
  depicted. See [documentation captures](#documentation-captures).
- Generated boot service and desktop startup entry pass `systemd-analyze verify`
  and `desktop-file-validate`. Installed CLI/Python/systemctl ownership was verified
  read-only. After the user enabled startup and rebooted, read-only journal checks
  confirmed the service connected successfully at 14:31:45 on 2026-10-30. The
  desktop's location request had failed at 14:31:37 before DNS was ready. The UI
  fix was verified with simulated startup timing; no further reboot or change to
  the host VPN, installed boot helpers or firewall was performed for this fix.
- Python syntax and Ruff checks passed. Generated desktop entries validated with
  `desktop-file-validate`. The local repository uses `main` and is linked through
  `origin` to `https://github.com/r-vage/CyberGhost-Linux-UI.git`.

## Primary references

- [CyberGhost Linux CLI overview](https://support.cyberghostvpn.com/hc/en-us/articles/360020835894-CyberGhost-VPN-CLI-App-Linux-FAQ)
- [Official CLI installation](https://support.cyberghostvpn.com/hc/en-us/articles/360020436274-Set-Up-CyberGhost-VPN-CLI-App-on-Linux)
- [Country and city selection](https://support.cyberghostvpn.com/hc/en-us/articles/360020673194-Select-a-Country-or-City-with-CyberGhost-VPN-CLI-App-on-Linux)
- [CyberGhost streaming commands](https://support.cyberghostvpn.com/hc/de/articles/360020741974-So-streamst-du-mit-der-CLI-App-von-CyberGhost-VPN-unter-Linux)
- [GTK application lifecycle](https://docs.gtk.org/gtk3/class.Application.html)
- [polkit pkexec authorization and exit codes](https://www.freedesktop.org/software/polkit/docs/0.110/pkexec.1.html)
- [nftables rules, atomic transactions and interface matching](https://netfilter.org/projects/nftables/manpage.html)
- [WireGuard routing and kill-switch guidance](https://git.zx2c4.com/wireguard-tools/about/src/man/wg-quick.8)
- [nftables socket cgroup matching](https://www.netfilter.org/projects/nftables/manpage.html)
- [systemd service lifecycle](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
- [Desktop application autostart specification](https://specifications.freedesktop.org/autostart/latest/)
- [PyGObject distro installation guidance](https://pygobject.gnome.org/getting_started.html)
- [Ptyxis standalone command execution](https://manpages.debian.org/unstable/ptyxis/ptyxis.1.en.html)

Tray integration is optional and requires an AppIndicator host. This system uses
Cinnamon and has Ayatana AppIndicator bindings; other desktops may need an
indicator extension or `--no-tray`. GTK 3 is used because its system bindings are
already installed here. The launcher deliberately avoids the ComfyUI environment.

The connection panel and tray share packaged SVG variants of the app shield:
green arrow for a verified connection, red cross for disconnection, and amber
question mark for unknown status or an ongoing connection change/boot job. Both
use the same state/description helper. A tooltip and accessible description
accompany the panel icon. Firewall/startup-setting changes alone do not replace
the connection state.
Demo indicators use a separate ID and clearly marked descriptions. Icons were
rendered at 16/24/32/96 pixels on light and dark backgrounds, and loaded through the
actual AppIndicator/GTK APIs in an isolated demo session; no real VPN changes were
performed for these checks.

The location list loads small SVG country flags from the packaged
`assets/flags` directory, with the country name/code exposed to accessibility
tools and a country-code badge if a flag is unavailable. The complete two-letter
4:3 set from flag-icons is bundled with its MIT license and source information;
flags require no network access. Package data includes the SVGs and notices.

The distro review also compared shell/package-manager detection in the user's
ComfyUI Linux installer. This app launches with POSIX sh rather than depending on
the user's login shell. Only CyberGhost's official installer requires Bash, located
with executable discovery. Distro identification reads os-release as data and
uses ID/ID_LIKE before falling back to installed package managers; no shell config
is sourced or modified. Dependency guidance is visible in Setup and `--check`.
Actual runtime verification remains on Linux Mint, with other distro environments
simulated in tests. CyberGhost's vendor-supported CLI list and the app's Python
minimum are separate requirements; no end-to-end compatibility is claimed for
Arch, openSUSE, immutable systems or containers without a working vendor CLI.


## Optional authorization retention

Setup can install a fixed helper snapshot under
`/usr/local/libexec/cyberghost-desktop/control` and the dedicated
`io.github.rvage.CyberGhostDesktop.control` polkit action. Only active-session VPN
controls use `auth_admin_keep`; the generic `org.freedesktop.policykit.exec` policy
and sudo configuration are untouched. Polkit owns the short retention period.

The executable annotation names the fixed root-owned launcher, never a general
interpreter. Its dispatcher accepts only validated connect selections, disconnect,
and boolean VPN-only changes. The caller account comes from `PKEXEC_UID`; the CLI
and installed modules must be root-owned and not writable by ordinary users.
Protected connections use the same scoped reconnect implementation described above.
Server switches verify disconnection before starting the new connection.

Installation/update/removal runs through a separate, non-cached authorization.
The GUI checks ownership and source hashes before using the installed snapshot;
outdated or missing helpers fall back to ordinary per-action authorization. Setup
offers an update or removal. Tests use temporary installation directories and
mocked authorization; they do not install policies on the development host.

- [Polkit authorization retention and action scope](https://polkit.pages.freedesktop.org/polkit/polkit.8.html)
- [pkexec executable annotations](https://polkit.pages.freedesktop.org/polkit/pkexec.1.html)


### Reconnect error reporting

CyberGhost CLI 1.5.0 has failure branches that exit with status zero, including
WireGuard setup and unavailable-server failures. A zero exit is therefore not
proof of a working connection. The protected helper verifies the interface,
route and WireGuard mark before refreshing protection. If verification fails,
Activity retains the captured CLI output instead of replacing it with advice
to enable VPN only. Monitor-verification failures retain the connection output
as well. The temporary cgroup exception is removed in both cases.

The reconnect namespace test now runs the system's actual `wg-quick` against a
temporary configuration and dummy peer. It verifies the resulting interface,
IPv4 route, firewall mark and DNS-update calls inside isolated network and mount
namespaces. It does not exercise CyberGhost's live account or VPN endpoints.


The runtime directory can be on a `noexec` `/run` filesystem. A plain bind mount
inherits that flag, so the DNS wrapper previously failed with permission denied;
`wg-quick` then removed the new interface. The wrapper's **private bind target**
now receives `remount,bind,ro,exec,nosuid,nodev`. This permits that root-owned
wrapper to execute without changing `/run` or the host DNS executable. The
namespace test reproduces the permission error before the fix and verifies the
complete WireGuard startup afterward, while confirming that the runtime source
remains `noexec` and the parent executable remains unchanged.

Reference: [mount's per-mount bind flags](https://man7.org/linux/man-pages/man8/mount.8.html).


### Server list loading

City totals arrive separately from individual server entries. The server popup
shows a loading indicator during the first fetch. Returning to a previously viewed
city displays its cached entries immediately while refreshing loads in the
background. A refresh preserves a still-present selected server and replaces rows
without duplication. Stale responses cannot clear a newer loading indicator.

While disconnected with VPN-only enabled, uncached server lists are deferred
instead of saved as empty lists. Once connection status confirms the new tunnel,
the selected city's pending list is fetched automatically. A failed refresh keeps
its cached entries and identifies the failure. `tests/gui_server_loading.py` checks
these cases with controlled futures and the real GTK widgets.

## Documentation captures

`tools/capture_docs.py` refreshes `docs/desktop.png`, `docs/setup.png`,
`docs/startup.png` and `docs/updates.png`, and renders five annotated pages in `docs/tour/` for the
[visual tour](Visual_Tour.md). It uses the actual GTK widget tree with Cairo/Pango
for the surrounding titles, numbered callouts and outlines. There is no ComfyUI,
browser or image-generation dependency.

Run with the system Python and the GTK/PyGObject/Cairo packages used by the app:

```sh
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 tools/capture_docs.py
```

To review captures before replacing repository images:

```sh
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 tools/capture_docs.py --output /tmp/cyberghost-docs
```

The script always uses the demo client, non-persistent preferences, demo firewall
and startup managers, plus simulated Setup and update states. Subprocess, setup
and online update actions are blocked during capture. Its example servers, public
addresses and available version are not read from the host VPN or online services.

For a separate GTK display, start this in one terminal:

```sh
broadwayd --address=127.0.0.1 --port=8088 :8
```

Then run the capture in another terminal:

```sh
PYTHONDONTWRITEBYTECODE=1 G_DEBUG=fatal-criticals \
GDK_BACKEND=broadway BROADWAY_DISPLAY=:8 \
/usr/bin/python3 tools/capture_docs.py --output /tmp/cyberghost-docs
```

Stop that temporary `broadwayd` with Ctrl+C when finished. Review all nine images
at full size, especially popover placement, expanded sections and callout targets.
GTK theme/font differences may slightly change dimensions; captures wait for
layout to stabilize and reject hidden targets or overlapping callout boxes.
