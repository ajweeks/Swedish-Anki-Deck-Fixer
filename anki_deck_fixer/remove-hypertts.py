import requests
from typing import List, Dict, Any
import re
import argparse

# --- HyperTTS sound tag removal script ---
# This script connects to Anki via AnkiConnect and removes sound tags of the form
# [sound:hypertts-<anything>.mp3] from the Front and Back fields of notes in a deck.

HYPERTTS_SOUND_RE = re.compile(r"\[sound:hypertts-[^\]]*?\.mp3\]")

url: str = "http://localhost:8765"


def anki_request(action: str, **params):
    payload = {
        "action": action,
        "version": 6,
        "params": params,
    }
    try:
        response: requests.Response = requests.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise Exception(f"AnkiConnect error: {data['error']}")
        return data.get("result")
    except requests.exceptions.ConnectionError:
        raise Exception(
            "Cannot connect to Anki. Make sure Anki is running with AnkiConnect add-on installed."
        )


def find_cards_in_deck(deck_name: str) -> List[int]:
    return anki_request("findCards", query=f'deck:"{deck_name}"')


def cards_info(card_ids: List[int]) -> List[Dict[str, Any]]:
    if not card_ids:
        return []
    return anki_request("cardsInfo", cards=card_ids)


def update_note_fields(note_id: int, fields: Dict[str, str]) -> Dict:
    note = {"id": note_id, "fields": fields}
    return anki_request("updateNoteFields", note=note)


def remove_hypertts_tags(text: str) -> str:
    if not text:
        return text
    new_text = HYPERTTS_SOUND_RE.sub("", text)
    new_text = re.sub(r"  +", " ", new_text).strip()
    return new_text

def clean_text(text: str) -> str:
    # Remove <div> tags and &nbsp; and trim
    return re.sub(r"<div>.*?</div> | &nbsp;", "", text).strip()

def chunked(iterable: List[int], n: int):
    for i in range(0, len(iterable), n):
        yield iterable[i : i + n]


def process_deck(deck_name: str, dry_run: bool = False) -> Dict[str, Any]:
    # Validate deck exists
    decks = anki_request("deckNames")
    if deck_name not in decks:
        raise Exception(f"Deck '{deck_name}' not found. Available: {decks}")

    print(f"Scanning deck: {deck_name}")
    card_ids = find_cards_in_deck(deck_name)
    print(f"Found {len(card_ids)} cards")

    seen_notes = set()
    notes_updated = 0
    fields_updated = 0
    tags_removed_total = 0

    for batch in chunked(card_ids, 250):
        info_list = cards_info(batch)
        print("Processing batch of", len(info_list), "cards")
        for card in info_list:
            note_id = card.get("note")
            if note_id in seen_notes:
                continue
            seen_notes.add(note_id)

            fields_obj: Dict[str, Dict[str, Any]] = card.get("fields", {})
            changed_fields: Dict[str, str] = {}

            for field_name in ("Front", "Back"):
                if field_name in fields_obj:
                    old_val = fields_obj[field_name].get("value", "")
                    new_val = remove_hypertts_tags(old_val)
                    new_val = clean_text(new_val)
                    if new_val != old_val:
                        changed_fields[field_name] = new_val
                        fields_updated += 1
                        tags_removed_total += 1

            if changed_fields:
                notes_updated += 1
                if dry_run:
                    print(
                        f"[DRY-RUN] Would update note: {[(fields_obj[f[0]].get('value', ''), f[1]) for f in changed_fields.items()]}"
                    )
                else:
                    update_note_fields(note_id, changed_fields)
                    print(
                        f"Updated note {note_id}: {list(changed_fields.keys())}: {[f for f in changed_fields.values()]}"
                    )

    summary = {
        "deck": deck_name,
        "notes_considered": len(seen_notes),
        "notes_updated": notes_updated,
        "fields_updated": fields_updated,
        "tags_removed": tags_removed_total,
        "dry_run": dry_run,
    }
    print("\nSummary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Remove [sound:hypertts-*.mp3] tags from Front/Back fields in an Anki deck"
    )
    parser.add_argument(
        "--deck", default="Default", help="Deck name to process (default: Default)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without applying updates",
    )
    args = parser.parse_args()

    try:
        process_deck(args.deck, dry_run=args.dry_run)
    except Exception as e:
        print(f"Error: {e}")
        raise


if __name__ == "__main__":
    main()
