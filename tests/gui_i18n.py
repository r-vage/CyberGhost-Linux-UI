# Exercise live language changes on real GTK widgets with demo-only backends.

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberghost_desktop.app import Application, Gio, GLib  # noqa: E402
from cyberghost_desktop.authorization import AuthorizationStatus  # noqa: E402
from cyberghost_desktop.preferences import Preferences  # noqa: E402
from cyberghost_desktop.setup_dialog import SetupDialog  # noqa: E402
from cyberghost_desktop.setup_tasks import Installation  # noqa: E402


def until(condition):
    if condition():
        return
    loop = GLib.MainLoop()
    failed = []

    def check():
        if condition():
            loop.quit()
            return False
        return True

    def timeout():
        failed.append(True)
        loop.quit()
        return False

    tick = GLib.timeout_add(10, check)
    deadline = GLib.timeout_add_seconds(8, timeout)
    loop.run()
    if failed:
        GLib.source_remove(tick)
        raise AssertionError("GTK translation test timed out")
    GLib.source_remove(deadline)


def external_action(*_args, **_kwargs):
    raise AssertionError("Translation tests must not invoke real system commands")


with (tempfile.TemporaryDirectory() as directory,
      patch("subprocess.run", side_effect=external_action),
      patch("subprocess.Popen", side_effect=external_action)):
    path = Path(directory) / "preferences.json"
    saved = Preferences(path)
    saved.data["language"] = "zh-CN"
    saved.save()
    app = Application(demo=True, tray=False, preferences=Preferences(path))
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    try:
        app.activate()
        window = app.window
        assert {row[1]: row[0] for row in window.language.get_model()} == {
            code: catalog.name for code, catalog in app.translator.catalogs.items()
        }, "Every discovered catalog must appear under its native name"
        assert window.language.get_active_id() == "zh-CN"
        assert window.connect_button.get_label() == "连接"
        assert not window.client.connected, "Restoring a language must not connect a VPN"
        until(lambda: len(window.locations) == 8 and window.state.state == "disconnected")
        germany = next(row for row in window.location_list.get_children() if row.location.code == "DE")
        window.location_list.select_row(germany)
        until(lambda: len(window.city.get_model()) == 3)
        window.city.set_active_id("Frankfurt")
        until(lambda: len(window.server.get_model()) == 2)
        window.server.set_active_id("frankfurt-s415-i01")
        window.favorite_button.set_active(True)
        window.connect_button.clicked()
        until(lambda: window.state.state == "connected" and not window.control_busy)
        window.guard_toggle.set_active(True)
        until(lambda: window.guard_state.state == "on" and not window.control_busy)
        window.startup_guard.set_active(True)
        window.startup_toggle.set_active(True)
        until(lambda: window.startup_state.enabled and not window.control_busy)
        window.startup_expander.set_expanded(True)
        window.search.set_text("germany")

        selection = window._selection()
        command = window.command_label.get_text()
        addresses = (window.before_ip, window.current_ip)
        requests, epoch = dict(window._requests), window._epoch
        buffer = window.log.get_buffer()
        log = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

        known_labels = {
            "de": ("Verbinden", "Stadt automatisch wählen", "Demo · Verbunden"),
            "zh-CN": ("连接", "自动选择城市", "演示 · 已连接"),
            "en": ("Connect", "Automatic city", "Demo · Connected"),
        }
        for code, catalog in app.translator.catalogs.items():
            labels = catalog.messages
            connect, automatic, active = known_labels.get(code, (
                labels["Connect"], labels["Automatic city"],
                labels["Demo · {status}"].format(status=labels["Connected"]),
            ))
            window.language.set_active_id(code)
            assert window.connect_button.get_label() == connect
            assert window.city.get_model()[0][0] == automatic
            assert window.status_title.get_text() == active
            assert window._selection() == selection, "Language must preserve protocol, city and server"
            assert window.command_label.get_text() == command, "Commands must never be translated"
            assert (window.before_ip, window.current_ip) == addresses
            assert window._requests == requests and window._epoch == epoch, "Language must not refresh or reconnect"
            assert window.client.connected and window.guard_state.state == "on"
            assert window.startup_state.enabled and window.startup_guard.get_active()
            assert window.favorite_button.get_active() and window.startup_expander.get_expanded()
            assert window.search.get_text() == "germany"
            assert buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False) == log
            assert Preferences(path).data["language"] == code

        window.language.set_active_id("zh-CN")
        assert "负载" in window.server._label.get_text()
        assert "已保存开机节点" in window.startup_detail.get_text()
        assert "已启用" in window.guard_label.get_text()
        assert "已连接" in window.status_icon.get_tooltip_text()
        window._apply_status(window.state)
        assert window.status_title.get_text() == "演示 · 已连接", "Later callbacks must use the selected language"

        installation = Installation("/usr/bin/cyberghostvpn", Path("/home/example/.cyberghost/config.ini"), "present",
                                    "/usr/bin/pkexec", "/usr/bin/sudo", ("/usr/bin/xterm", "-e"))
        with (patch("cyberghost_desktop.setup_dialog.setup_tasks.inspect_installation", return_value=installation),
              patch("cyberghost_desktop.setup_dialog.authorization.status",
                    return_value=AuthorizationStatus("off", "Linux asks for authorization for each VPN change."))):
            dialog = SetupDialog(window)
            window.setup_dialog = dialog
            assert dialog.get_title() == "设置 CyberGhost"
            assert dialog.check_button.get_label() == "重新检查"
            assert "命令行客户端已安装" in dialog.cli_status.get_text()
            window.language.set_active_id("de")
            assert dialog.get_title() == "CyberGhost einrichten"
            assert dialog.close_button.get_label() == "Schließen"
            assert dialog.account_status.get_text() == "Kontokonfiguration gefunden"
            dialog.recheck()
            assert dialog.check_button.get_label() == "Erneut prüfen"
            for code, catalog in app.translator.catalogs.items():
                window.language.set_active_id(code)
                assert dialog.get_title() == catalog.messages["Set up CyberGhost"]
                assert dialog.close_button.get_label() == catalog.messages["Close"]
                assert dialog.account_status.get_text() == catalog.messages["Account configuration found"]
                dialog.recheck()
                assert dialog.check_button.get_label() == catalog.messages["Check again"]
            dialog.destroy()
            window.setup_dialog = None
        window.language.set_active_id("zh-CN")
    finally:
        if app.window:
            app.window.destroy()
        app.quit()
    assert not app.ui._properties, "Destroyed windows must release their translation bindings"

print(f"GTK checks passed for {len(app.translator.catalogs)} languages: live labels, setup, status, "
      "choices, command preservation, preferences and cleanup.")
