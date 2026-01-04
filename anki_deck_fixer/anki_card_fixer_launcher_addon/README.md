# Anki Card Fixer Launcher Add-on

This add-on adds a Tools menu entry that launches your external fixer script
`anki_deck_fixer.py`, passing:

- the current card's deck via `--deck`
- the text on the current card's front via `--word_list`
- `--batch-size 1` to scope the run to this card

Your fixer script already supports these flags, so this avoids any interactive prompts.

## Installation

1. Copy the entire folder `anki_card_fixer_launcher_addon/` into your Anki add-ons directory:
   - Windows: `%APPDATA%\Anki2\addons21\anki_card_fixer_launcher_addon`
   - macOS: `~/Library/Application Support/Anki2/addons21/anki_card_fixer_launcher_addon`
   - Linux: `~/.local/share/Anki2/addons21/anki_card_fixer_launcher_addon`
2. Restart Anki.

You should now see Tools -> "Run Card Fixer on Current Card".
Start a review session, then invoke the action while viewing the target card.

## Configuration

By default the add-on will try to run:

- Python: `python` (from PATH)
- Script: `d:\\Code\\anki\\anki_deck_fixer\\anki_deck_fixer.py`

You can override these via environment variables before launching Anki:

- `ANKI_DECK_FIXER_PY` — path to Python executable (e.g. `C:\\Python311\\python.exe` or `py` on Windows)
- `ANKI_DECK_FIXER_SCRIPT` — absolute path to `anki_deck_fixer.py`

## Notes

- The fixer script requires `ANTHROPIC_API_KEY` in the environment. Ensure it is set
ahead of time in the environment Anki inherits when launching.
- The add-on extracts the front text by first trying the rendered question HTML and
then falling back to the first field referenced by the front template (or the first
field in the note). HTML is stripped and the result is truncated at 800 chars for safety.
- The script treats `--word_list` as a comma-separated list. If your front text contains
commas, it will be split into multiple entries; this is generally fine, as the script
searches the deck with `front:*<entry>*` for each entry.
