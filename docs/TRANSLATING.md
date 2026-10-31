# Translating CyberGhost Desktop

The app includes 15 interface languages. Use the language selector
at the top left of the main window to switch immediately. The choice is saved in
the normal preferences file; demo mode does not save it. Changing language updates
display text while keeping the current selection, connection and protection state.

## Included languages

Each catalog currently covers all 221 interface messages, including setup,
tooltips and protection guidance. The selector uses the native name in each file.

| Language | Selector name | Editable catalog |
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

## Add a language

1. Copy [`en.json`](../cyberghost_desktop/locales/en.json) in
   [`cyberghost_desktop/locales`](../cyberghost_desktop/locales) to a new file such as
   `nl.json` for Dutch.
2. Set `language` to the filename without `.json`. Set `name` to the language's
   native name, such as `Nederlands`. This name appears in the selector.
3. Translate the **values** in `messages`. Keep the English keys unchanged.
4. Run the checks below and reopen the app to discover the new file.
5. Submit the JSON file in a pull request. Mention the language and which screens
   you checked. Screenshots are welcome. No Python change or build step is needed
   to register another language.

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
/usr/bin/python3 tools/check_translations.py
/usr/bin/python3 -m unittest discover -s tests -v
sh ./run.sh --demo --no-tray
```

Choose the new language in the demo window. Check the main controls, tooltips,
server picker, status messages, startup section and expanded command/activity
sections. The demo does not invoke CyberGhost or polkit. A normal app launch also
lets you inspect Setup without installing anything; installation and VPN changes
require explicit actions.

The checker reports message coverage and rejects malformed JSON, duplicate keys,
invalid metadata, unknown source keys and mismatched placeholders. Partial
translations are supported: missing or invalid entries use the English source
text. A structurally invalid catalog is skipped, and a saved unavailable language
falls back to English. To require complete coverage, use:

```sh
/usr/bin/python3 tools/check_translations.py --complete
```

Catalogs are read at launch. Reopen the app after editing a file. Switching between
already loaded languages is immediate and does not restart the app or reconnect
the VPN.

To exercise the existing languages in a graphical session:

```sh
/usr/bin/python3 tests/gui_i18n.py
```

To capture the main, startup and setup screens with isolated demo data, replace
`nl` below with the code of your catalog:

```sh
/usr/bin/python3 tools/capture_docs.py --language nl --readme-only --output /tmp/cyberghost-nl
```

The capture tool uses the real GTK widgets with fake installation and connection
state. It blocks external commands and does not install software or change the
VPN. It needs GTK, Cairo and a graphical session, as described in
[documentation captures](implementation.md#documentation-captures).

## Maintain existing translations

Edit the corresponding JSON file and run the same checks. If an English source
message changes in the UI, update its key and value in `en.json` and update that key
in the other catalogs. For Python UI work, bind display properties through
`UIStrings` and use `message("Text with {name}", name=value)` for variable text, so
the app can translate the full sentence and update it during a language switch.
Keep command construction and CLI identifiers independent of display text.

[English README](../README.md) · [Deutsche README](../README_DE.md) · [中文说明](../README_ZH.md)
