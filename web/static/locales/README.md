# Agent-Forge Localization (i18n)

This directory contains localization dictionary files for the Agent-Forge web dashboard.

## Structure

Each language is defined as a JSON file named `<lang_code>.json`:
- `en.json` / `eng_us.json`: English (default base dictionary)
- `sk.json`: Slovak / Slovenčina
- `sk_qwerty.json`: Slovak QWERTY layout / example custom localization

## Adding a Custom Language (e.g. `sk_qwerty`, `de`, `pl`, `cs`)

1. Copy `en.json` (or any existing language file) to your new file:
   ```bash
   cp web/static/locales/en.json web/static/locales/my_lang.json
   ```
2. Edit `web/static/locales/my_lang.json` and translate the values.
3. Commit and push your new file to Git:
   ```bash
   git add web/static/locales/my_lang.json
   git commit -m "i18n: add my_lang localization"
   git push origin main
   ```

## Activating in Dashboard

In the Agent-Forge dashboard:
- **Quick switch**: Click the language button in the top bar (e.g. `EN` / `SK`) to cycle through installed locales.
- **Custom locale code**: Right-click the language button in the top bar and enter your locale code (e.g. `sk_qwerty` or `my_lang`). The dashboard will fetch `/static/locales/<code_name>.json`, store your preference in browser storage (`localStorage`), and update the entire UI dynamically without requiring a page reload.
