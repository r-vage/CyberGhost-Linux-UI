# Translating CyberGhost Desktop

The app includes 15 interface languages. Use the language selector
at the top left of the main window to switch immediately. The choice is saved in
the normal preferences file; demo mode does not save it. Changing language updates
display text while keeping the current selection, connection and protection state.

## Included languages

Each catalog currently covers all 249 interface messages, including setup,
updates, tooltips and protection guidance. The selector uses the native name in each file.

| Language | Selector name | Bundled catalog |
| --- | --- | --- |
| Arabic | العربية | [ar.json](../cyberghost_desktop/locales/ar.json) |
| English | English | [en.json](../cyberghost_desktop/locales/en.json) |
| French | Français | [fr.json](../cyberghost_desktop/locales/fr.json) |
| German | Deutsch | [de.json](../cyberghost_desktop/locales/de.json) |
| Hebrew | עברית | [he.json](../cyberghost_desktop/locales/he.json) |
| Italian | Italiano | [it.json](../cyberghost_desktop/locales/it.json) |
| Japanese | 日本語 | [ja.json](../cyberghost_desktop/locales/ja.json) |
| Korean | 한국어 | [ko.json](../cyberghost_desktop/locales/ko.json) |
| Persian | فارسی | [fa.json](../cyberghost_desktop/locales/fa.json) |
| Portuguese (Brazil) | Português (Brasil) | [pt-BR.json](../cyberghost_desktop/locales/pt-BR.json) |
| Russian | Русский | [ru.json](../cyberghost_desktop/locales/ru.json) |
| Spanish | Español | [es.json](../cyberghost_desktop/locales/es.json) |
| Turkish | Türkçe | [tr.json](../cyberghost_desktop/locales/tr.json) |
| Chinese (Simplified) | 简体中文 | [zh-CN.json](../cyberghost_desktop/locales/zh-CN.json) |
| Chinese (Traditional) | 繁體中文 | [zh-TW.json](../cyberghost_desktop/locales/zh-TW.json) |

## Personal translations and updates

On normal startup, the app creates editable copies in
`~/.config/cyberghost-desktop/locales`, or
`$XDG_CONFIG_HOME/cyberghost-desktop/locales` when that variable is set. Edit these
files for personal wording. They live outside Git, so they survive app updates
and replacing a ZIP installation. Reopen the app to load your edits.

The app compares three versions of each message: the previous default, your
current text and the new default. It merges at startup, including after updates:

| Change | Result in your personal file |
| --- | --- |
| A new message was added | Add it, even if you edited other messages in the file. |
| An untouched default was improved | Use the improved translation. |
| You customized a message | Keep your wording. |
| You and upstream changed the same message | Keep your wording and record the conflicting key for review. |
| An obsolete message was removed | Remove it if unchanged; retain customized text. |
| A language exists only in your personal folder | Add new English messages until you translate them. |

The personal folder contains these maintenance files:

- `.manifest.json` records the app version, hashes, previous defaults and conflicting
  message keys in each catalog's `conflicts` list.
- `.upstream/<language>.json` holds the latest defaults for comparison. To accept
  a conflicting translation, copy that value into your personal language file.
- `.backups/<language>/<sha256>.json` preserves the exact contents of customized
  files before an automatic rewrite, and of bundled language edits before an
  in-app update. Backups with identical contents are reused.

You do not need to edit these maintenance files. If the manifest is missing or
damaged, the app keeps existing wording and adds missing messages conservatively.
An invalid personal JSON file is left untouched; the UI uses bundled translations
until you fix it. Individual invalid entries fall back to the bundled language,
then English. These files are data and never execute code.

If you previously edited `cyberghost_desktop/locales` directly, normal startup
copies those edits into your personal translations. In-app installation also
backs up valid, unstaged language changes and restores their tracked defaults
before the Git update. New, untracked language files are moved into the personal
folder with backups. Staged translation work, local commits and other source
changes require a manual update, preserving work intended for a pull request.

To restore all default wording for a bundled language, close the app, move its
personal JSON file elsewhere, and reopen the app. It will recreate that file.

## Add a language

For personal use, copy `en.json` in your personal folder to a new filename such
as `nl.json`, set `language` to `nl`, change `name` to `Nederlands`, and translate
the message values. Reopen the normal app: the new language appears automatically.
No manifest entry or Python edit is required. Demo mode reads only bundled files
and does not read or write personal translations.

To share a translation in a pull request:

1. Copy [`en.json`](../cyberghost_desktop/locales/en.json) in
   [`cyberghost_desktop/locales`](../cyberghost_desktop/locales) to a new file such as
   `nl.json` for Dutch.
2. Set `language` to the filename without `.json`. Set `name` to the language's
   native name, such as `Nederlands`. This name appears in the selector.
3. Translate the **values** in `messages`. Keep the English keys unchanged.
4. Run `tools/check_translations.py --write-manifest` with Python to validate the
   catalogs and refresh `cyberghost_desktop/locale_manifest.json`. Reopen the demo
   app to discover and preview the new file.
5. Submit the language JSON and generated bundled manifest in a pull request.
   Mention the language and which screens you checked. Screenshots are welcome.
   Do not submit personal `.manifest.json`, backups or `.upstream` files.
   No Python change is needed to register another language.

For example, this is a valid partial catalog:

```json
{
  "language": "nl",
  "name": "Nederlands",
  "messages": {
    "Connect": "Verbinden",
    "Disconnect": "Verbinding verbreken",
    "{count} of {total} locations": "{count} van {total} locaties"
  }
}
```

Use UTF-8 JSON with double quotes and no comments or trailing commas. Language
codes use a lowercase two- or three-letter language followed by optional subtags
separated with hyphens, for example `de` or `zh-CN`. The filename and `language`
field must match exactly.

## Preserve placeholders and technical names

Keep every named placeholder, including its braces: `{count}`, `{total}`, `{city}`,
`{path}`, `{target}`, and so on. You may move them to fit the sentence. Do not rename
them or use Python expressions, attribute access, conversions or format specifiers.
Literal braces must be doubled as `{{` and `}}`.

Preserve commands, command-line options, file paths, keyboard shortcuts, product
names and protocol names in the text. Use `\n` for a newline inside a JSON string.
Messages are plain text, so do not add HTML or Markdown formatting. Keep button
labels short and translate full sentences naturally, including the guidance about
VPN-only protection and closing the window.

For Arabic, Persian and Hebrew, enter text in its normal reading order. GTK handles
bidirectional text rendering; the app's panel layout remains the same. Check
sentences containing server names, paths and numbers in the live preview. Glyph
coverage depends on the fonts installed on your system.

The right-to-left catalogs use Unicode direction isolates in the server labels:
`\u2067` starts a right-to-left segment, `\u2066` starts a left-to-right segment,
and `\u2069` ends either one. Preserve these JSON escapes around counts and loads
so server names, numbers and percent signs stay in the intended order.

Country, city, streaming-service and server identifiers come from the official CLI
and are displayed as supplied. App-generated server counts and load labels are
translated. Raw CLI and system diagnostics are preserved; some technical errors
may therefore remain in English. The official installer, polkit prompts and GTK's
built-in file chooser controls follow their own language settings.

## Check and preview

From the repository root:

```sh
/usr/bin/python3 tools/check_translations.py --write-manifest
/usr/bin/python3 -m unittest discover -s tests -v
sh ./run.sh --demo --no-tray
```

Choose the new language in the demo window. Check the main controls, tooltips,
server picker, status messages, startup section and expanded command/activity
sections, plus the About and updates dialog. The demo does not invoke CyberGhost
or polkit, and online update checks are disabled. A normal app launch also
lets you inspect Setup without installing anything; installation and VPN changes
require explicit actions.

The checker reports message coverage and rejects malformed JSON, duplicate keys,
invalid metadata, unknown source keys, mismatched placeholders and a stale bundled
manifest. Partial
translations are supported: missing or invalid entries use the English source
text. A structurally invalid catalog is skipped, and a saved unavailable language
falls back to English. To require complete coverage, use:

```sh
/usr/bin/python3 tools/check_translations.py --complete --write-manifest
```

Catalogs are read at launch. Reopen the app after editing a file. Switching between
already loaded languages is immediate and does not restart the app or reconnect
the VPN.

To exercise the existing languages in a graphical session:

```sh
/usr/bin/python3 tests/gui_i18n.py
/usr/bin/python3 tests/gui_locale_store.py
/usr/bin/python3 tests/gui_updates.py
```

To capture the main, startup, setup and update screens with isolated demo data, replace
`nl` below with the code of your catalog:

```sh
/usr/bin/python3 tools/capture_docs.py --language nl --readme-only --output /tmp/cyberghost-nl
```

The capture tool uses the real GTK widgets with fake installation and connection
state. It blocks external commands and does not install software or change the
VPN. It needs GTK, Cairo and a graphical session, as described in
[documentation captures](implementation.md#documentation-captures).

## Maintain existing translations

Edit the corresponding bundled JSON file and run the same checks. If an English source
message changes in the UI, update its key and value in `en.json` and update that key
in the other catalogs. For Python UI work, bind display properties through
`UIStrings` and use `message("Text with {name}", name=value)` for variable text, so
the app can translate the full sentence and update it during a language switch.
Keep command construction and CLI identifiers independent of display text.

Regenerate the bundled manifest after changing catalogs or the app version.
Its schema version, app version and SHA-256 hashes identify untouched shipped
files; the personal manifest supplies the previous message values for merging.
The normal checker (without `--write-manifest`) verifies that this generated file
matches the checkout. Keep new keys in `en.json` even when another language is
still incomplete, so personal catalogs receive them on the next startup.

[English README](../README.md) · [Deutsche README](../README_DE.md) · [中文说明](../README_ZH.md)
