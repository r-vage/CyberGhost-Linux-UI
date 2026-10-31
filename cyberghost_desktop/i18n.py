# Data-only translation catalogs, independent of GTK and privileged helpers.

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from string import Formatter

CATALOG_DIRECTORY = Path(__file__).with_name("locales")
LANGUAGE_CODE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")


@dataclass(frozen=True)
class Message:
    source: str
    values: dict = field(default_factory=dict)


def message(source, **values):
    return Message(source, values)


def placeholders(text):
    names = set()
    for _literal, name, spec, conversion in Formatter().parse(text):
        if name is not None:
            if not name.isidentifier() or spec or conversion:
                raise ValueError("Use simple named placeholders such as {count}.")
            names.add(name)
    return names


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


@dataclass
class Catalog:
    language: str
    name: str
    messages: dict
    errors: list = field(default_factory=list)


def catalog_data(body, code):
    data = json.loads(body, object_pairs_hook=unique_object)
    if not isinstance(data, dict):
        raise ValueError("A catalog must be a JSON object.")
    language, name, entries = data.get("language"), data.get("name"), data.get("messages")
    if not isinstance(language, str) or not LANGUAGE_CODE.fullmatch(language) or language != code:
        raise ValueError("The language code must match the filename, for example de.json or zh-CN.json.")
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError("The language name must be a short, nonempty string.")
    if not isinstance(entries, dict):
        raise ValueError("messages must be a JSON object.")
    return data


def load_catalog(path):
    data = catalog_data(path.read_text(encoding="utf-8"), path.stem)
    catalog = Catalog(data["language"], data["name"], {})
    for source, translated in data["messages"].items():
        try:
            if not source or not isinstance(translated, str) or not translated.strip():
                raise ValueError("Translations must be nonempty strings.")
            if placeholders(source) != placeholders(translated):
                raise ValueError("Translation placeholders must match the English text.")
        except ValueError as error:
            catalog.errors.append(f"{source!r}: {error}")
        else:
            catalog.messages[source] = translated
    return catalog


class Translator:
    def __init__(self, language="en", directory=CATALOG_DIRECTORY, *, user_directory=None):
        self.catalogs = {"en": Catalog("en", "English", {})}
        self.errors = []
        for path in sorted(Path(directory).glob("*.json")):
            if path.name.startswith("."):
                continue
            try:
                catalog = load_catalog(path)
            except (OSError, ValueError) as error:
                self.errors.append(f"{path.name}: {error}")
                continue
            self.catalogs[catalog.language] = catalog
            self.errors.extend(f"{path.name}: {error}" for error in catalog.errors)
        if user_directory is not None:
            for path in sorted(Path(user_directory).glob("*.json")):
                if path.name.startswith("."):
                    continue
                try:
                    catalog = load_catalog(path)
                except (OSError, ValueError) as error:
                    self.errors.append(f"{path}: {error}")
                    continue
                bundled = self.catalogs.get(catalog.language)
                messages = dict(bundled.messages) if bundled else {}
                messages.update(catalog.messages)
                self.catalogs[catalog.language] = Catalog(catalog.language, catalog.name, messages, catalog.errors)
                self.errors.extend(f"{path}: {error}" for error in catalog.errors)
        self.set_language(language)

    def set_language(self, language):
        self.language = language if language in self.catalogs else "en"

    def text(self, source, **values):
        return self.render(message(source, **values))

    def render(self, value):
        if value is None:
            return None
        source = value.source if isinstance(value, Message) else value
        translated = self.catalogs[self.language].messages.get(source, source)
        if not isinstance(value, Message) or not value.values:
            return translated
        values = {key: self.render(item) if isinstance(item, Message) else item for key, item in value.values.items()}
        return translated.format(**values)
