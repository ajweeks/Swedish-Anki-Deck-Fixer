# Anki add-on: Launch external card fixer with current card's front text
#
# This add-on adds a Tools menu action that launches the external
# script anki_deck_fixer.py, passing the current card's deck and the
# text on the card's front as a filter via --word_list.
#
# Adjust SCRIPT_PATH below if your script lives in a different location.

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

from aqt import mw, gui_hooks  # type: ignore
from aqt.qt import QAction, QKeySequence, Qt  # type: ignore
try:  # Anki 2.1.50+
    from aqt.qt import qconnect  # type: ignore
except Exception:  # older Anki
    qconnect = None  # type: ignore
from aqt.utils import showInfo, showWarning  # type: ignore

# Absolute path to your fixer script
SCRIPT_PATH = r"d:\\Code\\anki\\anki_deck_fixer\\anki_deck_fixer.py"

# Optional: override via environment variable to avoid hardcoding
SCRIPT_PATH = os.environ.get("ANKI_DECK_FIXER_SCRIPT", SCRIPT_PATH)

# Optional: Python executable. By default, rely on 'python' in PATH.
PYTHON_EXE = os.environ.get("ANKI_DECK_FIXER_PY", "python")

# Default keyboard shortcut
RUN_SHORTCUT = os.environ.get("ANKI_DECK_FIXER_SHORTCUT", "Ctrl+Alt+F")


def _strip_html(html: str) -> str:
    # Very simple HTML stripper; good enough for Anki question text
    text = re.sub(r"<[^>]+>", " ", html or "")
    # collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # unescape a few entities
    text = (text
            .replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">"))
    return text


def _tokens_from_text(text: str, max_tokens: int = 3) -> list[str]:
    """Extract up to max_tokens meaningful tokens for search.
    - Keep Unicode word chars (incl. accents)
    - Prefer longer tokens, dedupe while preserving order
    """
    # Split into word tokens
    toks = re.findall(r"\w+", text, flags=re.UNICODE)
    # Filter short tokens, keep order, dedupe
    seen = set()
    unique = []
    for t in toks:
        if len(t) < 2:
            continue
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        unique.append(t)
    if not unique:
        return []
    # Prefer longest tokens first, but keep relative order among equals
    unique_sorted = sorted(unique, key=lambda s: (-len(s), unique.index(s)))
    # Choose top-N and restore original order among the chosen
    chosen = set(unique_sorted[:max_tokens])
    return [t for t in unique if t in chosen]


def _front_text_from_template(card) -> Optional[str]:
    """Try to extract a field used on the front template.
    Falls back to first field if detection fails.
    """
    try:
        note = card.note()
        model = note.model()
        qfmt = card.template().get("qfmt", "") if hasattr(card, "template") else ""
        # Find first {{...}} reference on front
        # Handles filters like {{cloze:Text}} or {{text:Field}}
        m = re.search(r"{{[^{}:|]+:(?P<f>[^{}|]+)}}|{{(?P<f2>[^{}:|]+)}}", qfmt)
        field_name = None
        if m:
            field_name = (m.group("f") or m.group("f2") or "").strip()
        if not field_name:
            # fallback: first field
            field_name = model["flds"][0]["name"]
        val = note.get(field_name)
        if isinstance(val, str):
            return _strip_html(val)
    except Exception:
        pass
    return None


def _get_current_card_and_front_text() -> tuple[Optional[object], Optional[str], Optional[str]]:
    """Return (card, deck_name, front_text) if a card is active in Reviewer."""
    reviewer = getattr(mw, "reviewer", None)
    if not reviewer or not getattr(reviewer, "card", None):
        return None, None, None

    card = reviewer.card

    deck_name = "Default"

    # Prefer rendered question text if available
    front_text = None
    try:
        if hasattr(card, "q"):
            # some builds expose q() method
            html = card.q(reload=False)  # type: ignore[arg-type]
            front_text = _strip_html(html)
        elif hasattr(card, "question"):
            html = getattr(card, "question")
            front_text = _strip_html(html)
    except Exception:
        front_text = None

    # Fallback to field-based extraction
    if not front_text:
        front_text = _front_text_from_template(card)

    if front_text:
        # Avoid passing exceedingly long strings to shell; truncate conservatively
        front_text = front_text[:800]

    return card, deck_name, front_text


def _launch_fixer(deck_name: str, front_text: str) -> None:
    if not os.path.isfile(SCRIPT_PATH):
        showWarning(f"Card Fixer script not found:\n{SCRIPT_PATH}\n\nSet ANKI_DECK_FIXER_SCRIPT env var or edit the add-on to adjust the path.")
        return

    # Derive a compact word list from the front text to match the card
    toks = _tokens_from_text(front_text, max_tokens=3)
    word_list = ",".join(toks) if toks else front_text[:100]

    args = [
        PYTHON_EXE,
        SCRIPT_PATH,
        "--deck", deck_name,
        "--no-backup",
        "--web",
        "--word_list", word_list,
    ]

    # Launch in script directory so any relative paths resolve
    cwd = os.path.dirname(SCRIPT_PATH)

    # Preserve current environment; the script uses ANTHROPIC_API_KEY
    env = os.environ.copy()

    try:
        # Use Popen so Anki UI remains responsive
        subprocess.Popen(args, cwd=cwd, env=env)
        showInfo("Launched Card Fixer for current card. You can follow progress in the external console.")
    except FileNotFoundError:
        # Likely 'python' not in PATH
        showWarning("Failed to launch Python. Ensure 'python' is in your PATH, or set ANKI_DECK_FIXER_PY env var to your Python path.")
    except Exception as e:
        showWarning(f"Failed to launch Card Fixer:\n{e}")


def on_run_fixer_action() -> None:
    card, deck_name, front_text = _get_current_card_and_front_text()
    if not card:
        showWarning("No active card. Start a review session and try again.")
        return
    if not deck_name:
        showWarning("Could not determine deck name for current card.")
        return
    if not front_text:
        showWarning("Could not extract front text from current card.")
        return

    _launch_fixer(deck_name, front_text)


# Add reviewer-specific shortcut (compat across Anki versions)
def _add_reviewer_shortcuts(shortcuts, reviewer) -> None:
    # shortcuts is a list of (key, callback)
    shortcuts.append((RUN_SHORTCUT, on_run_fixer_action))

# Register to whichever hook exists on this Anki build
try:
    if hasattr(gui_hooks, "reviewer_will_init_shortcuts"):
        gui_hooks.reviewer_will_init_shortcuts.append(_add_reviewer_shortcuts)
    elif hasattr(gui_hooks, "reviewer_did_init_shortcuts"):
        gui_hooks.reviewer_did_init_shortcuts.append(_add_reviewer_shortcuts)
except Exception:
    # If hooks API differs, we still have the global QAction shortcut below
    pass


# Editor integration: add a button and support running on the edited note
def _get_editor_deck_and_front_text(editor) -> tuple[Optional[str], Optional[str]]:
    try:
        note = editor.note
        if not note or not getattr(note, 'id', None):
            return None, None
        # Find a card for this note to determine deck and front template
        cids = mw.col.find_cards(f"nid:{note.id}")
        if not cids:
            return None, None
        card = mw.col.getCard(cids[0])
        deck_name = "Default"
        front_text = _front_text_from_template(card)
        if not front_text and hasattr(card, "question"):
            front_text = _strip_html(getattr(card, "question"))
        if front_text:
            front_text = front_text[:800]
        return deck_name, front_text
    except Exception:
        return None, None


def _on_editor_button(editor) -> None:
    deck_name, front_text = _get_editor_deck_and_front_text(editor)
    if not deck_name:
        showWarning("Could not determine deck for this note (ensure it has at least one card).")
        return
    if not front_text:
        showWarning("Could not extract front text for this note.")
        return
    _launch_fixer(deck_name, front_text)


def _add_editor_button(buttons, editor) -> None:
    btn = editor.addButton(
        icon=None,
        cmd="run_card_fixer",
        func=lambda ed=editor: _on_editor_button(ed),
        tip=f"Run Card Fixer ({RUN_SHORTCUT})",
        label="Fixer",
    )
    # Some Anki builds expect add-ons to append to the provided list
    try:
        buttons.append(btn)
    except Exception:
        pass


def _add_editor_button_direct(editor) -> None:
    try:
        editor.addButton(
            icon=None,
            cmd="run_card_fixer",
            func=lambda ed=editor: _on_editor_button(ed),
            tip=f"Run Card Fixer ({RUN_SHORTCUT})",
            label="Fixer",
        )
    except Exception:
        pass


# Register editor button with compatible hook
try:
    if hasattr(gui_hooks, "editor_did_init_buttons"):
        gui_hooks.editor_did_init_buttons.append(_add_editor_button)
    elif hasattr(gui_hooks, "editor_did_init"):
        gui_hooks.editor_did_init.append(_add_editor_button_direct)
except Exception:
    pass


# Register menu action under Tools
_action = QAction("Run Card Fixer on Current Card", mw)
if qconnect:
    qconnect(_action.triggered, on_run_fixer_action)  # type: ignore[arg-type]
else:  # pragma: no cover
    _action.triggered.connect(on_run_fixer_action)

# Assign global shortcut so it works in Reviewer and Editor
_action.setShortcut(QKeySequence(RUN_SHORTCUT))
# PyQt6 prefers Qt.ShortcutContext.ApplicationShortcut; fall back if not present
try:
    _action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
except Exception:
    try:
        _action.setShortcutContext(getattr(Qt, "ApplicationShortcut"))
    except Exception:
        pass

mw.form.menuTools.addAction(_action)
