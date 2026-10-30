# Changelog

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
