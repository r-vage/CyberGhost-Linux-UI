"""Small local preferences, separate from CyberGhost's credentials/configuration."""

import json
import os
from pathlib import Path
import tempfile


class Preferences:
    def __init__(self, path: Path | None = None, *, persistent: bool = True):
        self.path = path or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cyberghost-desktop" / "preferences.json"
        self.persistent = persistent
        self.data = {"mode": "traffic", "protocol": "wireguard", "transport": "UDP", "favorites": [], "last_location": ""}
        if persistent:
            try:
                stored = json.loads(self.path.read_text())
                if isinstance(stored, dict):
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
            except (OSError, ValueError):
                pass

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
