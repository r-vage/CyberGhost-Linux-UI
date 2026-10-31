# Preserve personal translations while merging new and improved default messages.

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

from . import __version__
from .i18n import CATALOG_DIRECTORY, LANGUAGE_CODE, catalog_data, unique_object

SOURCE_MANIFEST = Path(__file__).with_name("locale_manifest.json")


def digest(body):
    return hashlib.sha256(body).hexdigest()


def json_bytes(data):
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for codepoint in ("2066", "2067", "2069"):
        text = text.replace(chr(int(codepoint, 16)), "\\u" + codepoint)
    return text.encode("utf-8")


def atomic_write(path, body):
    if path.is_symlink():
        raise OSError(f"Refusing to replace a language-file symlink: {path}")
    if path.exists() and path.read_bytes() == body:
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".language-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def default_manifest(directory=CATALOG_DIRECTORY):
    return {"schema": 1, "version": __version__, "files": {
        path.name: digest(path.read_bytes()) for path in sorted(Path(directory).glob("*.json"))
        if not path.name.startswith(".")
    }}


def merge_catalog(base, personal, upstream):
    # Missing baselines are conservative: existing personal values win.
    old = base or {"messages": {}}
    result = deepcopy(personal)
    conflicts = []
    if personal["name"] == old.get("name"):
        result["name"] = upstream["name"]
    elif old.get("name") != upstream["name"] and personal["name"] != upstream["name"]:
        conflicts.append("name")
    messages = result["messages"]
    for source, value in upstream["messages"].items():
        if source not in messages or (source in old["messages"] and messages[source] == old["messages"][source]):
            messages[source] = value
        elif value != old["messages"].get(source) and messages[source] != value:
            conflicts.append(source)
    for source, value in old["messages"].items():
        if source not in upstream["messages"] and messages.get(source) == value:
            messages.pop(source, None)
    return result, conflicts


class LocaleStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.manifest_path = self.directory / ".manifest.json"
        self.errors = []
        self.state = {"schema": 1, "version": __version__, "catalogs": {}}
        try:
            state = json.loads(self.manifest_path.read_bytes(), object_pairs_hook=unique_object)
            if state.get("schema") == 1 and isinstance(state.get("catalogs"), dict):
                self.state["catalogs"] = state["catalogs"]
        except (OSError, ValueError, AttributeError):
            pass

    def _baseline(self, code):
        entry = self.state["catalogs"].get(code, {})
        try:
            data = entry["defaults"]
            body = json_bytes(data)
            if digest(body) == entry["defaults_sha256"]:
                return catalog_data(body, code)
        except (KeyError, TypeError, ValueError):
            pass
        return None

    def _backup(self, code, body):
        path = self.directory / ".backups" / code / (digest(body) + ".json")
        if path.exists() and path.read_bytes() != body:
            raise OSError("A language backup has conflicting contents")
        atomic_write(path, body)

    def _merge(self, code, upstream, base, *, trusted, observed_hash):
        path = self.directory / f"{code}.json"
        original = path.read_bytes() if path.exists() else None
        personal = catalog_data(original, code) if original is not None else deepcopy(upstream)
        merged, conflicts = merge_catalog(base, personal, upstream)
        previous = self.state["catalogs"].get(code, {})
        old_conflicts = previous.get("conflicts", []) if isinstance(previous, dict) else []
        for key in old_conflicts if isinstance(old_conflicts, list) else []:
            if not isinstance(key, str):
                continue
            if key == "name":
                unresolved = merged["name"] != upstream["name"]
            else:
                unresolved = key in upstream["messages"] and merged["messages"].get(key) != upstream["messages"][key]
            if unresolved:
                conflicts.append(key)
        if original is None or merged != personal:
            if original is not None and personal != base:
                self._backup(code, original)
            atomic_write(path, json_bytes(merged))
        if trusted:
            atomic_write(self.directory / ".upstream" / f"{code}.json", json_bytes(upstream))
        defaults = upstream if trusted else base
        self.state["catalogs"][code] = {
            "defaults": defaults,
            "defaults_sha256": digest(json_bytes(defaults)),
            "source_sha256": observed_hash,
            "conflicts": sorted(set(conflicts)),
        }

    def sync(self, defaults=CATALOG_DIRECTORY, source_manifest=SOURCE_MANIFEST):
        try:
            source = json.loads(Path(source_manifest).read_bytes(), object_pairs_hook=unique_object)
            hashes = source.get("files", {}) if source.get("schema") == 1 else {}
            if not isinstance(hashes, dict):
                hashes = {}
        except (OSError, ValueError, AttributeError):
            hashes = {}
        sources = {}
        bundled_codes = set()
        for path in sorted(Path(defaults).glob("*.json")):
            if path.name.startswith("."):
                continue
            bundled_codes.add(path.stem)
            try:
                raw = path.read_bytes()
                data = catalog_data(raw, path.stem)
                current_hash = digest(raw)
                trusted = hashes.get(path.name) == current_hash
                if not trusted:
                    self._backup(path.stem, raw)
                sources[path.stem] = (data, trusted, current_hash)
            except (OSError, ValueError) as error:
                self.errors.append(f"{path.name}: {error}")
        english, english_trusted, english_hash = sources.get("en", ({"messages": {}}, False, None))
        # A personal-only language also receives new feature keys, in English
        # until someone translates them. No registration or bundled file is needed.
        for path in sorted(self.directory.glob("*.json")):
            if path.name.startswith(".") or path.stem in bundled_codes:
                continue
            try:
                personal = catalog_data(path.read_bytes(), path.stem)
                sources[path.stem] = ({"language": path.stem, "name": personal["name"],
                                       "messages": english["messages"]}, english_trusted, english_hash)
            except (OSError, ValueError) as error:
                self.errors.append(f"{path.name}: {error}")
        for code, (data, trusted, current_hash) in sources.items():
            try:
                upstream = dict(data, messages={**english["messages"], **data["messages"]})
                has_fallback = bool(english["messages"].keys() - data["messages"].keys())
                self._merge(code, upstream, self._baseline(code), trusted=trusted and (not has_fallback or english_trusted),
                            observed_hash=current_hash)
            except (OSError, ValueError) as error:
                self.errors.append(f"{code}.json: {error}")
        try:
            atomic_write(self.manifest_path, json_bytes(self.state))
        except OSError as error:
            self.errors.append(str(error))
        return self.errors

    def adopt(self, code, edited_body, baseline_body=None):
        # Called before Git replaces an edited bundled catalog. Keep its exact
        # bytes in a backup, and merge its edits into the personal working copy.
        if not LANGUAGE_CODE.fullmatch(code):
            raise ValueError("Invalid language code")
        edited = catalog_data(edited_body, code)
        base = catalog_data(baseline_body, code) if baseline_body is not None else None
        self._backup(code, edited_body)
        self._merge(code, edited, base, trusted=False, observed_hash=digest(edited_body))
        atomic_write(self.manifest_path, json_bytes(self.state))
