#!/usr/bin/env python3
# Validate editable catalogs without GTK, a VPN, or third-party Python packages.

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberghost_desktop.i18n import CATALOG_DIRECTORY, Translator  # noqa: E402
from cyberghost_desktop.locale_store import SOURCE_MANIFEST, atomic_write, default_manifest, json_bytes  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Check translation JSON, placeholders and coverage.")
    parser.add_argument("--directory", type=Path, default=CATALOG_DIRECTORY)
    parser.add_argument("--complete", action="store_true", help="also fail on missing translations")
    parser.add_argument("--write-manifest", action="store_true", help="refresh the bundled language manifest after editing PR catalogs")
    args = parser.parse_args()
    translator = Translator(directory=args.directory)
    errors = list(translator.errors)
    english = translator.catalogs["en"].messages
    if not english:
        errors.append("en.json must contain the English source messages.")
    for source, text in english.items():
        if source != text:
            errors.append(f"en.json: source and value differ: {source!r}")
    for code, catalog in translator.catalogs.items():
        missing = set(english) - catalog.messages.keys()
        extra = catalog.messages.keys() - english.keys()
        print(f"{code} ({catalog.name}): {len(set(english) & catalog.messages.keys())}/{len(english)} messages")
        if missing:
            print(f"  {len(missing)} missing; the app will use English for these entries.")
            if args.complete:
                errors.append(f"{code}: missing translations: {', '.join(sorted(missing))}")
        if extra:
            errors.append(f"{code}: unknown source messages: {', '.join(sorted(extra))}")
    if args.directory.resolve() == CATALOG_DIRECTORY.resolve() and not errors:
        expected = json_bytes(default_manifest())
        if args.write_manifest:
            atomic_write(SOURCE_MANIFEST, expected)
        elif not SOURCE_MANIFEST.exists() or SOURCE_MANIFEST.read_bytes() != expected:
            errors.append("Language manifest is out of date. Run tools/check_translations.py --complete --write-manifest.")
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
