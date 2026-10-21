# Dashboard translations

One JSON file per language, `web/static/locales/<code>.json`, mapping a key to the text shown in the dashboard
(`"nav.overview": "Overview"`). The file's `lang.name` key is the name shown in the language menu. Values are plain text: the
dashboard shows `<`, `>` and `&` as typed and never treats them as markup.

| File | Language |
|---|---|
| `en.json` | English - the reference; every key exists here |
| `sk.json` | Slovak |
| `eng_us.json`, `sk_qwerty.json` | copies of `en` and `sk` that differ only in `lang.name`; they exist to show how a variant is added |

A key missing from a translation shows the English text built into the page, so a partial translation works and can grow later.
If a language file does not exist at all, the dashboard falls back to `en.json`.

## Add a language

1. Copy the English file and translate the values (leave the keys, and any `{placeholders}`, as they are):
   ```bash
   cp web/static/locales/en.json web/static/locales/de.json
   ```
2. Set `lang.name` to the language's own name (`Deutsch`).
3. Open a pull request with the new file - see [CONTRIBUTING.md](../../../CONTRIBUTING.md). `make check` also checks that your
   file is valid JSON.

## Use a language

- **Menu:** Settings → language, or the language button in the top bar, lists every file in this directory.
- **Try a file without installing it:** Settings → language → *Upload locale (.json)*. The file is stored on the dashboard server
  (the same place as the others) and applied at once.
- **By code:** *Enter code manually* (or right-click the language button) loads `web/static/locales/<code>.json`.

The choice is remembered in the browser (`localStorage`) and applied without a page reload.
