# Changelog

## 0.1.8 — 2026-11-03

- Allow private IPv4 and IPv6 local networks, link-local addresses and local Docker connections while VPN-only protection is enabled, including before boot connection and after tunnel loss. Keep direct external traffic blocked and require both endpoints of a local forwarded connection to be internal.
- Update protection status, tooltips and documentation in all supported interface languages to distinguish local access from VPN-only internet access. Verify host/container TCP connectivity and IPv4/IPv6 output and forwarding in isolated network namespaces.

## 0.1.7 — 2026-11-02

- Add an optional startup setting to block internet before networking starts. Keep ordinary IPv4/IPv6 and LAN traffic blocked while an isolated CyberGhost process connects, allow the verified WireGuard transport, and retain protection on failure. Install the early firewall and network-manager dependencies only when selected; preserve existing post-connect startup behavior. Translate the option into all 15 languages and verify boot handoff, rollback, DNS isolation and kernel traffic blocking.
- Show saved locations while refreshing the live catalog, including when WireGuard is connected with VPN-only enabled. Keep saved choices available through network/DNS failures and report whether the refresh is pending or failed.
- Preserve country and city selections during catalog retries, clear destinations removed by a fresh list, and log the first network failure's reason. Add a GTK regression covering connected VPN-only loading and recovery.
- Wait for boot-service inspection and connection completion before checking the public IP. Recheck VPN/firewall status after boot and discard earlier replies so a stale disconnected state cannot mislabel the VPN exit as the before-VPN IP.
- Defer uncached city requests until the location catalog recovers, avoiding a second startup DNS failure and traceback from the automatically restored country. Load the cities automatically once the catalog succeeds.
- Compare the current selection with a completed boot connection after verifying live VPN status, so an unchanged startup destination does not enable reconnecting. Label the connected-state action Reconnect and translate its instructions in all 15 languages.

## 0.1.6 — 2026-11-01

- Refresh all English, German and Chinese README screenshots and the five visual-tour pages for the compact header, ping controls and smaller VPN-only checkbox. Add header and ping callouts using simulated demo data.
- Limit the VPN-only checkbox's clickable area to the checkbox and its label, preventing accidental toggles from empty space across the connection card.
- Move the yellow app title to the left, keep only the close window button, and use matching square icons for language, Setup, Refresh and About. Keep the language selector in a popover and match Connect/Disconnect to the compact ping button height.
- Add a Start/Stop ping button beside Connect and Disconnect, below the current public IP, with a live millisecond readout. Measure ICMP latency to 1.1.1.1 over the current connection without administrator authorization.
- Cancel probes on Stop, connection changes and window close; ignore late results and honor VPN-only protection. Show missing-tool and permission failures, retain the last result after stopping, and use simulated values in demo mode.
- Translate the ping controls into all 15 languages and validate probe cancellation, timeouts and GTK behavior.

## 0.1.5 — 2026-10-31

- Fix update checks reporting an older version immediately after a push. Resolve the current main-branch commit through GitHub's API, then read the version from that exact commit, avoiding stale raw branch responses.
- Validate branch metadata and bound both requests. Test cached branch content, fresh commit lookups, malformed responses and network failures.

## 0.1.4 — 2026-10-31

- Show the current version and add on-demand update checks, installation and restart in About. Compare stable versions published on GitHub; keep network and Git work asynchronous. Demo mode disables update actions.
- Fast-forward official main-branch checkouts after checking version and ancestry. Preserve local work, back up and migrate unstaged translation edits, and require manual updates for other local changes or commits. Keep VPN/protection active and pause VPN controls until the app restarts.
- Create editable personal language files outside Git. Use versioned manifests, hashes and previous defaults to merge messages individually: add new feature keys, update untouched translations and retain custom wording. Save exact backups, conflict keys and current upstream copies. Personal-only languages also receive new English keys and are discovered at startup.
- Translate all update controls into 15 languages and document translation maintenance. Test real updates in temporary repositories, translation merging and backup failures, restart behavior and the GTK workflow with an active simulated VPN.

## 0.1.3 — 2026-10-31

- Add a language selector with 15 complete catalogs: Arabic, English, French, German, Hebrew, Italian, Japanese, Korean, Persian, Brazilian Portuguese, Russian, Spanish, Turkish, Simplified Chinese and Traditional Chinese. Translate controls, setup, tooltips and app status text immediately while preserving VPN state, server selections and command arguments. Save the choice outside demo mode.
- Load editable JSON translation catalogs automatically, with English fallback for missing or invalid entries. Include a contributor guide and a checker for JSON, placeholders and coverage.
- Add German and Simplified Chinese READMEs with localized GTK screenshots. Refresh the English screenshots and extend the capture tool with language selection.
- Check language persistence, fallback, live GTK updates and widget cleanup alongside the existing VPN UI tests.

## 0.1.2 — 2026-10-30

- Refresh the desktop, startup and setup screenshots with country flags, shared status icons and current controls. Add a five-part visual tour with numbered callouts for locations, connection protection, startup, activity and CLI setup, linked from the README.
- Add a reusable documentation capture script that renders real GTK screens with demo data, without ComfyUI or changes to the active VPN.

## 0.1.1 — 2026-10-30

- Add bundled country flags to the location list for offline display, with country-code fallback. Reuse the tray's connected, disconnected and unknown-state shield icons in the connection panel, keeping both indicators synchronized during connection changes and boot startup.
- Replace the oversized server dropdown with a compact scrolling picker that preserves the selected server. Keep Automatic first, sort servers by lowest load, and put unknown loads last. Show loading/refresh status inside the list, display saved servers immediately while refreshing, and resume deferred server requests after reconnecting.
- Enable Connect when the selected destination changes; disconnect and verify the old connection before starting the new one in a single authorized action. Allow WireGuard reconnects and server changes while VPN-only stays enabled. A temporary exception covers only the isolated CyberGhost connection process; other traffic remains blocked, including on connection failure. Reuse saved locations and cached server choices while disconnected.
- Add optional authorization caching in Setup: an app-specific polkit action retains VPN-control authorization briefly, typically about five minutes. Its fixed root-owned helper supports only connection and VPN-only changes; installation and updates require fresh authorization.
- Fix protected reconnects on systems where `/run` is mounted `noexec`: make only the private DNS wrapper executable so `resolvectl`/`resolvconf` can finish WireGuard setup. Preserve actual CLI failure output, including failures that exit successfully. VPN-only protection stays enabled.
- Detect missing firewall helpers after moving or renaming the app folder and show restart guidance. Preserve useful helper error details instead of incorrectly suggesting missing dependencies.

## 0.1.0 — 2026-10-30

- Repository and Python distribution named `CyberGhost-Linux-UI`; existing app launchers, preferences and service identifiers remain compatible.
- Initial desktop interface for the installed CyberGhost Linux CLI.
- Searchable country, streaming and P2P lists; favorites; city/server selection.
- WireGuard and OpenVPN controls with Linux authorization for connection changes.
- Preserve the invoking account after authorization so CyberGhost finds its existing configuration instead of looking under `/home/root`.
- Connection status, command preview, activity output, optional tray and demo mode. The tray uses the app's shield with a green arrow for connected, a red cross for disconnected and an amber question mark for unknown/changing status.
- Correct WireGuard status when the CLI's unprivileged check reports a false disconnection. Verify the CyberGhost interface and local IPv4 routing without authorization prompts; prevent repeated reconnects while status is unknown.
- User installer and GitHub/ZIP setup instructions, including updates, relocation and uninstall. Launchers work even when ZIP extraction drops executable permissions.
- Guided CLI setup with official download links, an installer picker, interactive account setup, automatic first-run checks and detection after installation without restarting. Credentials stay in the official CLI terminal.
- Linux distro and package-manager detection with matching dependency guidance and a read-only `--check` command. Detect Bash for CLI installation, support Ptyxis, report the Python minimum clearly, and disable boot controls on non-systemd systems while keeping manual VPN controls available.
- Public IPv4 comparison in the status panel, showing the address checked before connecting and the current exit. IP addresses stay in memory; late or failed checks do not overwrite connection status.
- Optional WireGuard kill switch, off by default, with authorized firewall changes and verified status. Protection survives disconnecting or closing the app until disabled or reboot; other firewall tables are preserved.
- Keep the locations panel at its chosen width while the right pane expands with the window; long city/server labels are shortened to fit.
- Optional connection during boot with one-time authorized setup, a saved location/protocol, optional VPN-only protection after connection and desktop launch at login. Protected helper copies run through systemd; configuring or disabling startup preserves the current connection. Boot progress prevents overlapping manual actions. Location loading waits for startup checks and automatically retries temporary network/DNS failures without flooding Activity with tracebacks; persistent errors remain visible. Failed connections are not automatically repeated.
