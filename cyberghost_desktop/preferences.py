"""Small local preferences, separate from CyberGhost's credentials/configuration."""

from dataclasses import asdict
import json
import re
import os
from pathlib import Path
import tempfile

from .cli import Choice, Location, MODES


def choice_key(value):
    # Only catalog identifiers are saved, never commands or credentials.
    if not isinstance(value, (list, tuple)):
        raise ValueError("Invalid catalog key.")
    if len(value) == 2:
        mode, country = value
    elif len(value) == 4 and value[0] == "servers":
        _, mode, country, city = value
        if not isinstance(city, str) or not city or len(city) > 160 or any(ord(c) < 32 for c in city):
            raise ValueError("Invalid city.")
    else:
        raise ValueError("Invalid catalog key.")
    if not isinstance(mode, str) or mode not in MODES or not isinstance(country, str) or not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("Invalid location.")
    return json.dumps(value, separators=(",", ":"))


class Preferences:
    def __init__(self, path: Path | None = None, *, persistent: bool = True):
        self.path = path or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cyberghost-desktop" / "preferences.json"
        self.persistent = persistent
        self.data = {"mode": "traffic", "protocol": "wireguard", "transport": "UDP", "vpn_only": True, "favorites": [], "last_location": "", "location_catalogues": {}, "choice_catalogues": {}, "language": "en"}
        if persistent:
            try:
                stored = json.loads(self.path.read_text())
                if isinstance(stored, dict):
                    if type(stored.get("vpn_only")) is bool:
                        self.data["vpn_only"] = stored["vpn_only"]
                    if isinstance(stored.get("language"), str) and re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", stored["language"]):
                        self.data["language"] = stored["language"]
                    if isinstance(stored.get("mode"), str) and stored["mode"] in {"traffic", "torrent", "streaming"}:
                        self.data["mode"] = stored["mode"]
                    if isinstance(stored.get("protocol"), str) and stored["protocol"] in {"wireguard", "openvpn"}:
                        self.data["protocol"] = stored["protocol"]
                    if isinstance(stored.get("transport"), str) and stored["transport"] in {"UDP", "TCP"}:
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
                    choices = stored.get("choice_catalogues", {})
                    if isinstance(choices, dict):
                        for key, entries in list(choices.items())[-64:]:
                            try:
                                key = choice_key(json.loads(key))
                            except (ValueError, TypeError):
                                continue
                            if not isinstance(entries, list):
                                continue
                            valid = [item for item in entries[:3000] if isinstance(item, dict)
                                     and set(item) == {"value", "label", "load"}
                                     and isinstance(item["value"], str) and 0 < len(item["value"]) <= 160
                                     and not item["value"].startswith("-") and not any(ord(c) < 32 for c in item["value"])
                                     and isinstance(item["label"], str) and len(item["label"]) <= 400
                                     and (item["load"] is None or type(item["load"]) is int and 0 <= item["load"] <= 100)]
                            # Preserve a successful empty response, but never
                            # turn corrupt data into a successful empty cache.
                            if not entries or valid:
                                self.data["choice_catalogues"][key] = valid
            except (OSError, ValueError):
                pass

    def remember_locations(self, mode, locations):
        self.data["location_catalogues"][mode] = [asdict(location) for location in locations[:3000]]

    def has_locations(self, mode):
        # The upgrade fallback in offline_locations is only one saved country,
        # not a fetched catalog. It must not suppress the first real download.
        return bool(self.data["location_catalogues"].get(mode))

    def remember_choices(self, key, choices):
        catalogues = self.data["choice_catalogues"]
        key = choice_key(key)
        catalogues.pop(key, None)
        catalogues[key] = [asdict(choice) for choice in choices[:3000]]
        while len(catalogues) > 64:
            del catalogues[next(iter(catalogues))]

    def choice_cache(self):
        return {tuple(json.loads(key)): [Choice(**item) for item in entries]
                for key, entries in self.data["choice_catalogues"].items()}

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
