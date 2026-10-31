"""Small local preferences, separate from CyberGhost's credentials/configuration."""

from dataclasses import asdict
import json
import re
import os
from pathlib import Path
import tempfile

from .cli import Location


class Preferences:
    def __init__(self, path: Path | None = None, *, persistent: bool = True):
        self.path = path or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cyberghost-desktop" / "preferences.json"
        self.persistent = persistent
        self.data = {"mode": "traffic", "protocol": "wireguard", "transport": "UDP", "favorites": [], "last_location": "", "location_catalogues": {}, "language": "en"}
        if persistent:
            try:
                stored = json.loads(self.path.read_text())
                if isinstance(stored, dict):
                    if isinstance(stored.get("language"), str) and re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", stored["language"]):
                        self.data["language"] = stored["language"]
                    if stored.get("mode") in {"traffic", "torrent", "streaming"}:
                        self.data["mode"] = stored["mode"]
                    if stored.get("protocol") in {"wireguard", "openvpn"}:
                        self.data["protocol"] = stored["protocol"]
                    if stored.get("transport") in {"UDP", "TCP"}:
                        self.data["transport"] = stored["transport"]
                    if isinstance(stored.get("last_location"), str):
                        self.data["last_location"] = stored["last_location"][:200]
                    if isinstance(stored.get("favorites"), list):
                        self.data["favorites"] = [s for s in stored["favorites"] if isinstance(s, str) and len(s) < 200][:500]
                    catalogues = stored.get("location_catalogues", {})
                    if isinstance(catalogues, dict):
                        for mode in ("traffic", "torrent", "streaming"):
                            entries = catalogues.get(mode, [])
                            if isinstance(entries, list):
                                valid = [item for item in entries[:3000] if isinstance(item, dict) and set(item) == {"name", "code", "service"}
                                         and all(isinstance(value, str) and len(value) <= 300 for value in item.values())
                                         and re.fullmatch(r"[A-Z]{2}", item["code"])]
                                if valid:
                                    self.data["location_catalogues"][mode] = valid
            except (OSError, ValueError):
                pass

    def remember_locations(self, mode, locations):
        self.data["location_catalogues"][mode] = [asdict(location) for location in locations[:3000]]

    def offline_locations(self, mode):
        cached = self.data["location_catalogues"].get(mode, [])
        if cached:
            return [Location(**entry) for entry in cached]
        # Upgrade from an older app: its last country still permits a protected
        # connection even before a catalogue has been cached by this version.
        saved = self.data["last_location"].split(":", 2)
        if len(saved) == 3 and saved[0] == mode and re.fullmatch(r"[A-Z]{2}", saved[1]):
            return [Location(saved[1], saved[1], saved[2])]
        return []

    def save(self) -> None:
        if not self.persistent:
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix=".preferences-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self.data, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
