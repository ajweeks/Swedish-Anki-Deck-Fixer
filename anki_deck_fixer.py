#!/usr/bin/env python3
"""
Anki Swedish Deck Fixer

This script safely processes an Anki deck to fix Swedish flashcards according to specific rules.
It uses AnkiConnect to interact with Anki and Claude API for intelligent card processing.

Prerequisites:
1. Install AnkiConnect add-on in Anki (code: 2055492159)
2. Install required packages: pip install requests anthropic
3. Set your Claude API key as environment variable: ANTHROPIC_API_KEY
4. Have Anki running with AnkiConnect enabled

Safety features:
- Creates backup deck before processing
- Processes cards in batches with review prompts
- Logs all changes for review
- Allows rollback if needed
"""

import json
import requests
import os
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
import anthropic
import sys
import difflib
import re
import argparse
import urllib.request
import urllib.parse
import traceback
import base64

class AnkiConnector:
    """Handles communication with Anki through AnkiConnect"""
    
    def __init__(self, url="http://localhost:8765"):
        self.url = url
        
    def request(self, action: str, **params) -> Dict[str, Any]:
        """Send request to AnkiConnect"""
        payload = {
            "action": action,
            "version": 6,
            "params": params
        }
        
        try:
            response = requests.post(self.url, json=payload)
            response.raise_for_status()
            result = response.json()
            
            if result.get("error"):
                raise Exception(f"AnkiConnect error: {result['error']}")
                
            return result.get("result")
        except requests.exceptions.ConnectionError:
            raise Exception("Cannot connect to Anki. Make sure Anki is running with AnkiConnect add-on installed.")
    
    def get_deck_names(self):
        """Get all deck names"""
        return self.request("deckNames")
    
    def get_cards_in_deck(self, deck_name: str):
        """Get all card IDs in a deck"""
        return self.request("findCards", query=f"deck:\"{deck_name}\"")
    
    def get_card_info(self, card_ids: List[int]) -> Dict[str, Any]:
        """Get card information"""
        return self.request("cardsInfo", cards=card_ids)
    
    def update_note_fields(self, note_id: int, fields: Dict[str, str])-> Dict[str, Any]:
        """Update note fields"""
        params = {
            "note": {
                "id": note_id,
                "fields": fields,
            }
        }
        
        return self.request("updateNoteFields", **params)
    
    def update_note_model(self, note_id: int, model_name: str, fields: Dict[str, str]) -> Dict[str, Any]:
        """Update note model and fields"""
        params = {
            "note": {
                "id": note_id,
                "modelName": model_name,
                "fields": fields
            }
        }
        return self.request("updateNoteModel", **params)
    
    def create_deck(self, deck_name: str):
        """Create a new deck"""
        return self.request("createDeck", deck=deck_name)
    
    def export_deck(self, deck_name: str, path: str):
        """Export deck to file"""
        # Ensure the path is absolute and in current directory
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        return self.request("exportPackage", deck=deck_name, path=path, includeSched=False)
    
    def get_note_info(self, note_ids: List[int]):
        """Get note information"""
        return self.request("notesInfo", notes=note_ids)
    
    def store_media_file(self, filename: str, data: bytes) -> bool:
        """Store media file in Anki's media collection"""
        try:
            encoded_data = base64.b64encode(data).decode('utf-8')

            params = {
                "filename": filename,
                "data": encoded_data
            }
            
            result = self.request("storeMediaFile", **params)
            return result is not None
        except Exception as e:
            print(f"Error storing media file {filename}: {e}")
            return False
    
class ForvoAPI:
    """Handles Forvo API requests for Swedish pronunciation audio"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.base_url = "https://apifree.forvo.com"
        self.session = requests.Session()
        
    def search_pronunciations(self, word: str, language: str = "sv") -> List[Dict]:
        """Search for pronunciations of a word"""
        if not self.api_key:
            return []
            
        url = f"{self.base_url}/key/{self.api_key}/format/json/action/word-pronunciations/word/{word}/language/{language}"
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('attributes', {}).get('total', 0) == 0:
                return []
                
            pronunciations = data.get('items', [])
            # Sort by votes (most voted first)
            pronunciations.sort(key=lambda x: x.get('votes', 0), reverse=True)
            
            return pronunciations[:3]  # Return top 3
            
        except Exception as e:
            print(f"Forvo API error for '{word}': {e}")
            return []
    
    def download_pronunciation(self, word: str) -> Optional[Dict[str, Any]]:
        """Download the best pronunciation for a word"""
        pronunciations = self.search_pronunciations(word)
        
        if not pronunciations:
            return None
            
        best = pronunciations[0]
        audio_url = best.get('pathmp3')
        
        if not audio_url:
            return None
            
        try:
            # Download the audio file
            response = self.session.get(audio_url, timeout=30)
            response.raise_for_status()
            
            # Generate filename
            filename = f"{word}_forvo_{best.get('id', 'unknown')}.mp3"
            # Clean filename for Anki
            filename = re.sub(r'[^\w\-_\.]', '_', filename)
            
            return {
                'filename': filename,
                'data': response.content,
                'word': word,
                'votes': best.get('votes', 0),
                'username': best.get('username', 'unknown')
            }
            
        except Exception as e:
            print(f"Error downloading audio for '{word}': {e}")
            return None

class DiffFormatter:
    """Formats text differences with colors"""
    
    @staticmethod
    def format_diff(old_text: str, new_text: str) -> str:
        """Format differences between old and new text with colors"""
        if old_text == new_text:
            return f"No changes: {old_text}"
        
        # Use difflib to get differences
        differ = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile='old',
            tofile='new',
            n=0
        )
        
        result = []
        for line in differ:
            # print("differ line: ", line, end='')
            if line.startswith('---') or line.startswith('+++') or line.startswith('@@'):
                continue
            elif line.startswith('-'):
                # Removed text in red
                result.append(f"\033[91m{line[1:].rstrip()}\033[0m")
            elif line.startswith('+'):
                # Added text in green  
                result.append(f"\033[92m{line[1:].rstrip()}\033[0m")
            else:
                result.append(line.rstrip())
        
        return '\n'.join(result) if result else f"Changed from: {old_text} → {new_text}"
    
    @staticmethod
    def print_field_changes(field_name: str, old_value: str, new_value: str):
        """Print field changes with formatting"""
        if old_value != new_value:
            print(f"{field_name}:")
            print(f"{DiffFormatter.format_diff(old_value, new_value)}")

offline_response = [
    {
        'card_id': 1660465378288,
        'updated_field_front': '',
        'updated_field_back': '1. Mature\n"Det var ett <i>moget</i> beslut"\n"Hon är en <i>mogen</i> person"\n"De mest <i>mogna</i> eleverna"\n\n2. Ripe\n"<i>Mogna</i> tomater"\n"Päronet är inte <i>moget</i> än"\n\n<span style="color: #c2c2c2">(syn: utvecklad, fullvuxen, vuxen; för frukt: mälld)</span>',
        'updated_field_audio': '[sound:forvo_mogen.mp3]',
        'model_change': 'Basic (with audio)',
        'uncertain_changes': [],
        'notes': 'The word can be used both for physical ripeness and mental/emotional maturity'
     }
    # {
    #   "card_id": 1660465378288,
    #   "updated_field_front": "en mogen (2)",
    #   "updated_field_back": "1. Mature<br>\"Det var ett <i>moget</i> beslut\"<br><br>2. Ripe<br>\"<i>Mogna</i> tomater ligger på bordet\"<br><br><img src=\"ripe_fruits.jpg\"><br><br><span style=\"color: #c2c2c2\">(syn: utvecklad, fullvuxen, vuxen; för frukt: mälld)</span>",
    #   "updated_field_audio": "[sound:forvo_mogen.mp3]",
    #   "model_change": "Basic (with audio)",
    #   "uncertain_changes": ["Not sure if the image addition is necessary since both meanings could be visualized differently"],
    #   "needs_flag_7": False
    # }
    # [
        # {
        #     "card_id": 123,
        #     "updated_field_front": "New front text",
        #     "updated_field_back": "New back text with more details",
        #     "updated_field_audio": "[sound:new_audio.mp3]",
        #     "model_change": "Basic (with audio)",
        #     "uncertain_changes": ["Uncertain about the definition of 'example'"],
        #     "needs_flag_7": False
        # }
    # ]
  ]

class SwedishCardProcessor:
    """Processes Swedish flashcards using Claude API"""
    
    def __init__(self, api_key: str, forvo_api_key: Optional[str] = None, anki_connector: Optional[AnkiConnector] = None):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.forvo = ForvoAPI(forvo_api_key)
        self.anki = anki_connector
        self.changes_log = []
        
    def process_card_batch(self, cards: List[Dict]) -> List[Dict]:
        """Process a batch of cards using Claude"""
        
        # Prepare card data for Claude
        card_data = []
        for card in cards:
            # Extract relevant fields
            all_fields = card.get('fields', {})
            fields = {
                'Front': all_fields.get('Front', {}).get('value', ''),
                'Back': all_fields.get('Back', {}).get('value', ''),
                'Audio': all_fields.get('Audio', {}).get('value', ''),
            }
            card_info = {
                'card_id': card.get('cardId'),
                'model_name': card.get('modelName'), # Card type (e.g., Basic, Basic (with audio))
                'fields': fields,
            }
            card_data.append(card_info)
        
        # Create prompt for Claude
        prompt = self._create_processing_prompt(card_data)
        
        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # print("Response from Claude:\n", response.content[0].text, "\n")
            
            # Parse Claude's response
            return self._parse_claude_response(response.content[0].text, cards)
            
        except Exception as e:
            print(f"✗ Error processing batch with Claude: {e}")
            print(traceback.format_exc())
            return []
    
    def _create_processing_prompt(self, card_data: List[Dict]) -> str:
        """Create the prompt for Claude to process cards"""
        
        rules = """
## Rules
You are a language expert tasked with helping me build a Swedish flash card deck that I study to memorize words and phrases. Your current task is to go through my deck and fix it up according to the following rules:
- A card ending in "autogenerated" should be entirely re-written. Find a definition and related words from wiktionary, synonyms from synonyms.se, and audio from forvo.
- Example sentences should be simply be in quotes, each on a new line. For example, `(t.ex. "Jag gillar att läsa")` should be changed to `"Jag gillar att läsa"`
- If a word has multiple defintions they should be formatted as a list using numbers each on a new line, such as 1. Definition A\n2. Definition B\n, and so on.
- When a word has more than one definition indicate that on the card front as the number in parenthesis. Example: Mogen (2)
- Synonyms should appear at the end of the back card in parenthesis as follows: `(syn: häpen, förvånad)`)
- Additional important and relevant information should also appear at the end of the back card as follows: `(se även: förvåning)".
- All extra text at the end of a card should use #c2c2c2 as the text colour.
- Very uncommon word usages should be indicated as such, coming last in the list of definitions after a "mindre vanliga: " line.
- Any sound tag beginning with "sound:hypertts" should be deleted.
- If the word can be visualized easily then an image can be retrieved and added to the card.
- Fix the spelling for any misspelled words.
- Example sentences should be wrapped in quotation marks and each on a new line. The front word should be in italics. The sentence (or partial sentence) should be as long as it needs to to show the word's usage but not longer. Example: "Det var ett <i>moget</i> beslut" (for the word "mogen").
- Example sentences should show different tenses, conjugations, and usages of the word.
- If a word is reflexive, indicate that with "(refl)" at the start of the line, after the number. Example: 1. (refl) Att klä på sig
- Very uncommon words that are unlikely to be seen or heard today can be marked with flag 7 ("sällsynt").
- If the front field contains an audio tag (starting with "[sound:") it should be removed and returned in "updated_field_audio". Otherwise leave that field empty.
- If a card type is "Basic" then it should be changed to "Basic (with audio)" (in the model_change field) so it has this additional field.
- Don't change the capitalization of card fronts unless absolutely necessary.
- If a word is a noun, always include the article (en or ett) in the front field.
- If a word is a verb, always include the preposition in the back field.
- Replace HTML codes like &quot; with actual quotes, &nbsp; with spaces, etc.

## General guidelines
Keep cards short and concise so they can be efficiently reviewed, yet with enough info to capture the precise meaning and usage of the word or phrase.
Only change fields that need changing, leave others as they are.

Important: Provide only the requested information in a json format, no additional text.
"""
        prompt = f"""{rules}

Please process the following cards and return the results in JSON format. For each card, return only the updated fields.

Cards to process:
{json.dumps(card_data, indent=2, ensure_ascii=False)}

Return format example:
{{
  "processed_cards": [
    {{
      "card_id": 123,
      "updated_field_front": "New front",  // Leave empty if no change
      "updated_field_back": "New back",  // Leave empty if no change
      "updated_field_audio": "Moved audio",  // Only fill with existing audio tag front Front card, or empty string if no change, do not invent a new tag
      "model_change": "Basic (with audio)",  // only if model needs to change
      "uncertain_changes": ["things you're uncertain about"],
      "notes": "Any additional important note or comment (if necessary)",
      "needs_flag_7": true  // if word should be marked as rare
    }}
  ]
}}
"""
        return prompt
    
    def _parse_claude_response(self, response_text: str, original_cards: List[Dict]) -> List[Dict]:
        """Parse Claude's JSON response and prepare updates"""
        try:
            # Extract JSON from response (Claude might wrap it in text)
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            
            if start_idx == -1 or end_idx == 0:
                print("No JSON found in Claude's response")
                return []
            
            json_str = response_text[start_idx:end_idx]
            parsed_response = json.loads(json_str)
            
            processed_cards = parsed_response.get('processed_cards', [])
            
            # Log changes
            for card in processed_cards:
                card_id = card.get('card_id')
                # changes = card.get('changes_made', [])
                uncertain = card.get('uncertain_changes', [])
                
                self.changes_log.append({
                    'card_id': card_id,
                    'timestamp': datetime.now().isoformat(),
                    # 'changes': changes,
                    'uncertain_changes': uncertain
                })
            
            return processed_cards
            
        except json.JSONDecodeError as e:
            print(f"Error parsing Claude's response as JSON: {e}")
            print("Raw response:", response_text[:500])
            return []
        except Exception as e:
            print(f"Error processing Claude's response: {e}")
            return []
    
    def _add_forvo_audio(self, card: Dict):
        """Add Forvo audio to a card if appropriate"""
        if not self.forvo.api_key or not self.anki:
            return
            
        updated_fields = card.get('updated_fields', {})
        front_field = updated_fields.get('Front', '')
        
        # Extract the main word from the front field (remove articles, parentheses, etc.)
        word = self._extract_main_word(front_field)
        
        if word and not updated_fields.get('Audio'):
            print(f"  Downloading audio for '{word}'...")
            
            audio_data = self.forvo.download_pronunciation(word)
            if audio_data:
                # Store the audio file in Anki's media collection
                if self.anki.store_media_file(audio_data['filename'], audio_data['data']):
                    # Create audio tag for Anki
                    audio_tag = f"[sound:{audio_data['filename']}]"
                    updated_fields['Audio'] = audio_tag
                    card['updated_fields'] = updated_fields
                    
                    # changes = card.get('changes_made', [])
                    # changes.append(f"Added Forvo audio for '{word}' ({audio_data['votes']} votes)")
                    # card['changes_made'] = changes
                    
                    print(f"  ✓ Audio added: {audio_data['filename']}")
                else:
                    print(f"  ✗ Failed to store audio file for '{word}'")
            else:
                print(f"  - No audio found for '{word}'")
    
    def _extract_main_word(self, front_field: str) -> str:
        """Extract the main Swedish word from the front field"""
        # Remove HTML tags
        clean_text = re.sub(r'<[^>]+>', '', front_field)
        
        # Remove articles (en, ett, den, det)
        clean_text = re.sub(r'^(en|ett|den|det)\s+', '', clean_text, flags=re.IGNORECASE)
        
        # Remove parentheses and their contents
        clean_text = re.sub(r'\([^)]*\)', '', clean_text)
        
        # Take the first word
        words = clean_text.strip().split()
        return words[0] if words else ''

class AnkiDeckFixer:
    """Main class to orchestrate the deck fixing process"""
    
    def __init__(self, claude_api_key: str, forvo_api_key: Optional[str] = None, should_create_backup: bool = True):
        self.anki = AnkiConnector()
        self.processor = SwedishCardProcessor(claude_api_key, forvo_api_key, self.anki)
        self.backup_created = False
        self.should_create_backup = should_create_backup
        
    def create_backup(self, deck_name: str) -> Optional[str]:
        """Create backup of the deck if enabled"""
        if not self.should_create_backup:
            print("Backup creation disabled")
            return None
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{deck_name}_backup_{timestamp}"
        backup_path = f"./{backup_name}.apkg"
        
        try:
            self.anki.export_deck(deck_name, backup_path)
            print(f"✓ Backup created: {backup_path}")
            self.backup_created = True
            return backup_path
        except Exception as e:
            print(f"✗ Failed to create backup: {e}")
            raise
    
    def process_deck(self, deck_name: str, batch_size: int = 10, start_from: int = 0, total_batches: int = -1, run_offline: bool = False):
        """Process the entire deck in batches"""
        
        # Verify deck exists
        deck_names = self.anki.get_deck_names()
        if deck_name not in deck_names:
            print(f"✗ Deck '{deck_name}' not found. Available decks: {', '.join(deck_names)}")
            return
        
        # Create backup if enabled
        backup_path = self.create_backup(deck_name)
        
        # Get all cards in deck
        card_ids = self.anki.get_cards_in_deck(deck_name)
        total_cards = len(card_ids)
        print(f"Found {total_cards} cards in deck '{deck_name}'")
        
        
        
        
        # TODO: Sort by review count (ascending), and then date created (ascending)
        # Place card with flag 1 ("behöver utvidgas") at the start of the list and cards with flag 7 ("sällsynt") at the end
        card_infos : Dict[str, Any] = self.anki.get_card_info(card_ids)
        card_infos_list = sorted([x for x in card_infos.values()], key=lambda c: (c.get('due', 0), c.get('id', 0)))
        card_ids = [c['cardId'] for c in card_infos_list]
        
        
        
        if start_from > 0:
            card_ids = card_ids[start_from:]
            print(f"Starting from card {start_from}")
        
        if total_batches != -1:
            card_ids = card_ids[0:total_batches*batch_size]
        else:
            total_batches = (len(card_ids) + batch_size - 1) // batch_size

        # Process in batches
        processed_count = 0
        for i in range(0, len(card_ids), batch_size):
            batch_card_ids = card_ids[i:i + batch_size]
            batch_num = i // batch_size + 1
            
            # Get card info
            try:
                print(f"\n--- Processing batch {batch_num}/{total_batches} ({len(batch_card_ids)} cards) ---")
                
                cards_info = self.anki.get_card_info(batch_card_ids)
                
                # Get unique note IDs and their info
                # note_ids = list(set([card['note'] for card in cards_info]))
                # notes_info = self.anki.get_note_info(note_ids)
                
                # Combine card and note info
                # enriched_cards = []
                # for card in cards_info:
                #     note_id = card['note']
                #     note_info = next((n for n in notes_info if n['noteId'] == note_id), {})
                #     card['note'] = note_info
                #     enriched_cards.append(card)
                
                # Process with Claude
                if run_offline:
                    print("Running in offline mode, skipping Claude processing")
                    processed_cards = offline_response
                else:
                    processed_cards = self.processor.process_card_batch(cards_info)

                if not processed_cards:
                    print("No changes suggested by Claude for this batch")
                    continue
                
                # Review changes before applying
                print(f"\nClaude suggests {len(processed_cards)} changes:")
                card_updates = []
                for new_card_info in processed_cards:
                    # Get the original card info to show the front field
                    # TODO: Turn cards_info into a dict for easier access
                    original_card = next((c for c in cards_info if c['cardId'] == new_card_info['card_id']), None)
                    if original_card:
                        card_update = {
                            'card_id': new_card_info['card_id']
                        }
                        
                        front_field = original_card['fields'].get('Front', 'Unknown').get('value', 'Unknown')
                        print(f"## {front_field}:")

                        # print("--------")
                        # print("new_card_info:", new_card_info)
                        # print("--------")
                        
                        # Show field changes with diff formatting
                        original_field_front = original_card['fields']['Front']['value'].strip()
                        original_field_back = original_card['fields']['Back']['value'].strip()
                        original_audio_back = original_card['fields'].get('Audio', {}).get('value', '').strip()
                        updated_field_front = new_card_info.get('updated_field_front', {})
                        updated_field_back = new_card_info.get('updated_field_back', {})
                        updated_field_audio = new_card_info.get('updated_field_audio', {})
                        
                        original_fields = {
                            'Front': original_field_front,
                            'Back': original_field_back,
                            'Audio': original_audio_back
                        }
                        updated_fields = {
                            'Front': updated_field_front if len(updated_field_front) > 0 else original_field_front,
                            'Back': updated_field_back if len(updated_field_back) > 0 else original_field_back,
                            'Audio': updated_field_audio if len(updated_field_audio) > 0 else original_audio_back,
                        }

                        for (field_name, new_value) in updated_fields.items():
                            # print(field_name)
                            # print("original value: ###", original_fields[field_name].replace("<br>", '\n'), "###")
                            # print("new_value: ###", new_value, "###")

                            original_value = original_fields[field_name].replace('<br>', '\n').strip()
                            new_value = new_value.replace('<br>', '\n').strip()

                            if new_value != original_value:
                                DiffFormatter.print_field_changes(field_name, original_value, new_value)
                        
                        if 'model_change' in new_card_info and len(new_card_info['model_change']) > 0:
                            print(f"\033[36mModel change: {new_card_info['model_change']}\033[0m")
                            card_update['model_change'] = new_card_info['model_change']
                        
                        if new_card_info.get('uncertain_changes'):
                            print(f"\033[93mUncertain: {', '.join(new_card_info['uncertain_changes'])}\033[0m")
                            card_update['uncertain_changes'] = new_card_info['uncertain_changes']
                        
                        print()

                        card_updates.append(card_update)
                
                # Ask for confirmation
                response = input(f"\nApply these changes to batch {batch_num}? (y/n/s=skip/q=quit): ").lower()
                
                if response == 'q':
                    print("Stopping processing.")
                    break
                elif response == 's':
                    print("Skipping this batch.")
                    continue
                elif response != 'y':
                    print("Skipping this batch.")
                    continue
                
                # Apply changes
                changes_applied = 0
                for card in processed_cards:
                    changed = False
                    
                    try:
                        # card_id: int = card.get('card_id', -1)
                        # note_id: int = self.anki.get_card_info([card_id])[0].get('note', -1)
                        # model_name = card.get('model_change', '')
                        
                        # print("------")
                        # print(card)
                        # print("------")
                        
                        card_id: int = card.get('card_id', -1)
                        note_id: int = self.anki.get_card_info([card_id])[0].get('note', -1)
                        original_card = next((c for c in cards_info if c['cardId'] == new_card_info['card_id']), None)
                        original_field_front = original_card['fields']['Front']['value'].strip()
                        original_field_back = original_card['fields']['Back']['value'].strip()
                        original_audio_back = original_card['fields'].get('Audio', {}).get('value', '').strip()
                        updated_field_front = card.get('updated_field_front', '')
                        updated_field_back = card.get('updated_field_back', '')
                        updated_field_audio = card.get('updated_field_audio', '')
                        model_change = card.get('model_change', '')
                        
                        if len(updated_field_back) > 0:
                            updated_field_back = updated_field_back.replace('\n', '<br>').strip()
                        
                        any_field_changed = (
                            len(updated_field_front) > 0 or
                            len(updated_field_back) > 0 or
                            len(updated_field_audio) > 0 or
                            len(model_change) > 0
                        )
                        if any_field_changed:
                            updated_fields = {
                                'Front': updated_field_front if len(updated_field_front) > 0 else original_field_front,
                                'Back': updated_field_back if len(updated_field_back) > 0 else original_field_back,
                                'Audio': updated_field_audio if len(updated_field_audio) > 0 else original_audio_back
                            }
                            
                            if (len(model_change) > 0):
                                self.anki.update_note_model(note_id, model_change, updated_fields)
                                changed = True
                            elif len(updated_fields) > 0:
                                self.anki.update_note_fields(note_id, updated_fields)
                                changed = True
                            
                    except Exception as e:
                        print(f"✗ Failed to update note {note_id}: {e}")

                    if changed:
                        changes_applied += 1
                    
                print(f"✓ Applied {changes_applied} changes in batch {batch_num}")
                processed_count += changes_applied
                
            except Exception as e:
                print(f"✗ Error processing batch {batch_num}: {e}")
                print(traceback.format_exc())
                continue
            
            # Small delay to be respectful to APIs
            time.sleep(1)
        
        print(f"=== Processing Complete ===")
        print(f"Total cards processed: {processed_count}")
        if backup_path:
            print(f"Backup created at: {backup_path}")
        else:
            print("No backup was created")
        
        # Save change log
        log_file = f"changes_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(self.processor.changes_log, f, indent=2, ensure_ascii=False)
        print(f"Change log saved to: {log_file}")

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Fix Swedish Anki flashcards using Claude AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python anki_deck_fixer.py                   # Interactive mode with backup
  python anki_deck_fixer.py --no-backup       # Interactive mode without backup
  python anki_deck_fixer.py --deck "Default"  # Process specific deck
  python anki_deck_fixer.py --batch-size 5    # Smaller batches
  python anki_deck_fixer.py --start-from 100  # Start from card 100

Environment Variables:
  ANTHROPIC_API_KEY   Required: Your Claude API key
  FORVO_API_KEY       Optional: Your Forvo API key for audio
        """
    )
    
    parser.add_argument('--deck', type=str, help='Deck name to process')
    parser.add_argument('--batch-size', type=int, default=10, help='Number of cards to process in each batch (default: 10)')
    parser.add_argument('--total-batches', type=int, help='How many batches to execute')
    parser.add_argument('--start-from', type=int, default=0, help='Card number to start from (default: 0)')
    parser.add_argument('--no-backup', action='store_true', help='Skip creating backup (not recommended)')
    parser.add_argument('--list-decks', action='store_true', help='List available decks and exit')
    parser.add_argument('--run-offline', action='store_true', help='Don\'t use Claude, use an fake response instead')
    
    return parser.parse_args()

def main():
    """Main entry point"""
    
    # Parse command line arguments
    args = parse_arguments()
    
    # Check for Claude API key
    claude_api_key = os.getenv('ANTHROPIC_API_KEY')
    if not claude_api_key:
        print("✗ Please set your ANTHROPIC_API_KEY environment variable")
        print("  Export it in your shell: export ANTHROPIC_API_KEY='your-key-here'")
        return
    
    # Check for optional Forvo API key
    forvo_api_key = os.getenv('FORVO_API_KEY')
    if forvo_api_key:
        print("✓ Forvo API key found - will add pronunciation audio")
    else:
        print("ℹ No Forvo API key found (FORVO_API_KEY) - audio features disabled")
    
    # Initialize fixer
    try:
        should_create_backup = not args.no_backup
        fixer = AnkiDeckFixer(claude_api_key, forvo_api_key, should_create_backup)
    except Exception as e:
        print(f"✗ Failed to initialize: {e}")
        return
    
    # Handle list decks option
    if args.list_decks:
        try:
            decks = fixer.anki.get_deck_names()
            print("Available decks:")
            for i, deck in enumerate(decks, 1):
                print(f"  {i}. {deck}")
        except Exception as e:
            print(f"✗ Error listing decks: {e}")
            print(traceback.format_exc())
        return
    
    total_batches = -1
    if args.total_batches:
        total_batches = args.total_batches

    # Get available decks
    try:
        decks = fixer.anki.get_deck_names()
        
        # Get deck selection
        if args.deck:
            deck_name = args.deck
            if deck_name not in decks:
                print(f"✗ Deck '{deck_name}' not found")
                print("Available decks:")
                for i, deck in enumerate(decks, 1):
                    print(f"  {i}. {deck}")
                return
        else:
            print("Available decks:")
            for i, deck in enumerate(decks, 1):
                print(f"  {i}. {deck}")
            deck_choice = input("\nEnter deck name or number: ").strip()
            
            # Parse choice
            if deck_choice.isdigit():
                deck_idx = int(deck_choice) - 1
                if 0 <= deck_idx < len(decks):
                    deck_name = decks[deck_idx]
                else:
                    print("Invalid deck number")
                    return
            else:
                deck_name = deck_choice
                if deck_name not in decks:
                    print(f"Deck '{deck_name}' not found")
                    return
        
        # Get processing options (or use command line args)
        batch_size = args.batch_size
        start_from = args.start_from
        
        # Show configuration
        print(f"\nConfiguration:")
        print(f"  Deck: {deck_name}")
        print(f"  Batch size: {batch_size}")
        if total_batches != -1:
            print(f"  Total batches: {total_batches}")
        print(f"  Start from: card {args.start_from}")
        print(f"  Backup: {'enabled' if should_create_backup else 'disabled'}")
        print(f"  Audio: {'enabled' if forvo_api_key else 'disabled'}")
        
        if not args.deck:
            confirm = input("\nProceed? (y/n): ").lower()
            if confirm != 'y':
                print("Cancelled.")
                return
        
        print(f"\nStarting to process deck '{deck_name}'")
        print("Press Ctrl+C at any time to stop safely")
        
        # Process the deck
        fixer.process_deck(deck_name, batch_size, start_from, total_batches, run_offline=args.run_offline)
        
    except KeyboardInterrupt:
        print("\n\nProcessing interrupted by user.")
        if fixer.backup_created:
            print("Your original deck is safely backed up.")
        elif should_create_backup:
            print("No backup was created as processing was interrupted early.")
    except Exception as e:
        print(f"✗ Error: {e}")
        print(traceback.format_exc())

if __name__ == "__main__":
    main()