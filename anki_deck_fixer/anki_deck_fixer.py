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
- Allows rollback if needed
"""

# TODO:
# [x] Always allow front field to be edited in web interface
# [x] Don't add new cards until the user explicitly requests it

import json
import requests
import os
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
import anthropic
import difflib
import re
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import webbrowser
from urllib.parse import urlparse
import traceback

MODEL_NAME = "claude-sonnet-4-5-20250929"

class AnkiConnector:
    """Handles communication with Anki through AnkiConnect"""

    def __init__(self, url="http://localhost:8765"):
        self.url = url

    def request(self, action: str, **params):
        """Send request to AnkiConnect"""
        payload = {"action": action, "version": 6, "params": params}

        try:
            # print(f"---action: {action}, params: {params}")
            response: requests.Response = requests.post(self.url, json=payload)
            response.raise_for_status()
            result = response.json()
            # print(f"-----result: {result}")

            if result.get("error"):
                raise Exception(f"AnkiConnect error: {result['error']}")

            r = result.get("result")
            return r
        except requests.exceptions.ConnectionError:
            raise Exception(
                "Cannot connect to Anki. Make sure Anki is running with AnkiConnect add-on installed."
            )

    def get_deck_names(self) -> Dict:
        """Get all deck names"""
        return self.request("deckNames")

    def get_cards_in_deck(self, deck_name: str) -> Dict:
        """Get all card IDs in a deck"""
        return self.request("findCards", query=f'deck:"{deck_name}"')

    def get_cards_in_deck_with_search(self, deck_name: str, search: str) -> Dict:
        """Get all card IDs in a deck"""
        return self.request("findCards", query=f'deck:"{deck_name}" {search}')

    def get_card_info(self, card_ids: List[int]) -> Dict:
        """Get card information"""
        return self.request("cardsInfo", cards=card_ids)

    def get_note_tags(self, note_id: int) -> Dict:
        """Get note tags"""
        params = {"note": note_id}
        return self.request("getNoteTags", **params)

    def update_note_tags(self, note_id: int, tags: List[str]) -> Dict:
        """Update note tags"""
        params = {"note": {"id": note_id, "tags": tags}}
        return self.request("updateNoteTags", **params)

    def remove_note_tag(self, note_id: int, tag_to_remove: str):
        """Remove specific tag from a note"""
        current_tags = self.get_note_tags(note_id)
        print("Current tags:", current_tags, "tag to remove: ", tag_to_remove)
        updated_tags = [tag for tag in current_tags if tag != tag_to_remove]
        print("Updated tags:", updated_tags)
        self.update_note_tags(note_id, updated_tags)

    def update_note(
        self, note_id: int, fields: Dict[str, str], tags: List[str]
    ) -> Dict:
        """Update note fields"""
        params = {"note": {"id": note_id, "fields": fields, "tags": tags}}
        print("updateNote: ", params)
        return self.request("updateNote", **params)

    def update_note_fields(
        self, note_id: int, fields: Dict[str, str], model_name: Optional[str] = None
    ) -> Dict:
        """Update note fields"""
        params = {"note": {"id": note_id, "fields": fields}}
        if model_name:
            params["note"]["modelName"] = model_name

        return self.request("updateNoteFields", **params)

    def add_note(
        self, deck_name: str, model_name: str, fields: Dict[str, str], tags: List[str]
    ) -> Dict:
        """Add a new note"""
        params = {
            "note": {
                "deckName": deck_name,
                "modelName": model_name,
                "fields": fields,
                "tags": tags,
                "options": {
                    "allowDuplicate": True,
                },
            }
        }
        return self.request("addNote", **params)

    def create_deck(self, deck_name: str) -> Dict:
        """Create a new deck"""
        return self.request("createDeck", deck=deck_name)

    def export_deck(self, deck_name: str, path: str) -> Dict:
        """Export deck to file"""
        # Ensure the path is absolute and in current directory
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        return self.request(
            "exportPackage", deck=deck_name, path=path, includeSched=False
        )

    def get_note_info(self, note_ids: List[int]) -> Dict:
        """Get note information"""
        return self.request("notesInfo", notes=note_ids)

    def store_media_file(self, filename: str, data: bytes) -> bool:
        """Store media file in Anki's media collection"""
        try:
            import base64

            encoded_data = base64.b64encode(data).decode("utf-8")

            result = self.request(
                "storeMediaFile", filename=filename, data=encoded_data
            )
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

            if data.get("attributes", {}).get("total", 0) == 0:
                return []

            pronunciations = data.get("items", [])
            # Sort by votes (most voted first)
            pronunciations.sort(key=lambda x: x.get("votes", 0), reverse=True)

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
        audio_url = best.get("pathmp3")

        if not audio_url:
            return None

        try:
            # Download the audio file
            response = self.session.get(audio_url, timeout=30)
            response.raise_for_status()

            # Generate filename
            filename = f"{word}_forvo_{best.get('id', 'unknown')}.mp3"
            # Clean filename for Anki
            filename = re.sub(r"[^\w\-_\.]", "_", filename)

            return {
                "filename": filename,
                "data": response.content,
                "word": word,
                "votes": best.get("votes", 0),
                "username": best.get("username", "unknown"),
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
            fromfile="old",
            tofile="new",
            n=0,
        )

        result = []
        for line in differ:
            if (
                line.startswith("---")
                or line.startswith("+++")
                or line.startswith("@@")
            ):
                continue
            elif line.startswith("-"):
                # Removed text in red
                result.append(f"\033[91m{line[1:].rstrip()}\033[0m")
            elif line.startswith("+"):
                # Added text in green
                result.append(f"\033[92m{line[1:].rstrip()}\033[0m")
            else:
                result.append(line.rstrip())

        return "\n".join(result) if result else f"Changed from: {old_text} → {new_text}"

    @staticmethod
    def print_field_changes(field_name: str, old_value: str, new_value: str):
        """Print field changes with formatting"""
        if old_value != new_value:
            print(f"\n  {field_name}:")
            print(f"    {DiffFormatter.format_diff(old_value, new_value)}")


class WebServer(BaseHTTPRequestHandler):
    """HTTP server to handle web interface requests"""

    fixer = None

    def do_GET(self):
        """Handle GET requests"""
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        try:
            if path == "/" or path == "/index.html":
                self.serve_interface()
            elif path == "/api/decks":
                self.serve_decks()
            elif path == "/api/status":
                self.serve_status()
            else:
                self.send_error(404)
        except Exception as e:
            print(f"Error handling GET {path}: {e}")
            traceback.print_exc()
            if path.startswith("/api/"):
                self.send_json_error(500, str(e))
            else:
                self.send_error(500, str(e))

    def do_POST(self):
        """Handle POST requests"""
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(post_data) if post_data else {}

            if path == "/api/process":
                self.handle_process_request(data)
            elif path == "/api/apply":
                self.handle_apply_request(data)
            elif path == "/api/retry":
                self.handle_retry_request(data)
            else:
                self.send_error(404)
        except Exception as e:
            print(f"Error handling POST {path}: {e}")
            traceback.print_exc()
            if path.startswith("/api/"):
                self.send_json_error(500, str(e))
            else:
                self.send_error(500, str(e))

    def serve_interface(self):
        """Serve the main HTML interface"""
        # Read the HTML content from the artifact or inline it
        html_content = self.get_interface_html()

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_content.encode("utf-8"))))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(html_content.encode("utf-8"))

    def serve_decks(self):
        """Serve list of available decks"""
        try:
            if not self.fixer:
                print("Error: Fixer not initialized")
                raise Exception("Fixer not initialized")

            decks = self.fixer.anki.get_deck_names()
            response = {"decks": decks}
            self.send_json_response(response)
        except Exception as e:
            print(f"Error getting decks: {e}")
            self.send_json_error(500, str(e))

    def serve_status(self):
        """Serve server status"""
        response = {
            "status": "running",
            "claude_api": bool(os.getenv("ANTHROPIC_API_KEY")),
            "forvo_api": bool(os.getenv("FORVO_API_KEY")),
            "anki_connected": False,
        }

        # Test Anki connection
        try:
            if self.fixer:
                self.fixer.anki.get_deck_names()
                response["anki_connected"] = True
            else:
                print("No fixer instance available")
        except Exception as e:
            print(f"Anki connection failed: {e}")

        self.send_json_response(response)

    def handle_process_request(self, data):
        """Handle card processing request"""
        try:
            deck_name = data.get("deck_name")
            batch_size = data.get("batch_size", 10)
            start_from = data.get("start_from", 0)
            create_backup = data.get("create_backup", True)
            word_list = data.get("word_list")
            flagged_only = data.get("flagged_only")

            if not deck_name:
                raise Exception("deck_name is required")

            if not self.fixer:
                raise Exception("Fixer not initialized")

            # Update the fixer's backup setting for this request
            original_backup_setting = self.fixer.should_create_backup
            self.fixer.should_create_backup = create_backup

            try:
                if word_list:
                    batch_size = len(
                        word_list
                    )  # Process all words in one go if word_list is provided
                    start_from = 0
                results = self.fixer.process_cards_for_review(
                    deck_name, batch_size, start_from, word_list, flagged_only
                )

                self.send_json_response(results)
            finally:
                # Restore original backup setting
                self.fixer.should_create_backup = original_backup_setting

        except Exception as e:
            print(f"Error in handle_process_request: {e}")
            traceback.print_exc()
            self.send_json_error(500, str(e))

    def handle_apply_request(self, data):
        """Handle apply changes request"""
        try:
            if not self.fixer:
                raise Exception("Fixer not initialized")

            results = self.fixer.apply_selected_changes(data)
            self.send_json_response(results)

        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_retry_request(self, data):
        """Handle retry card processing with additional instructions"""
        try:
            if not self.fixer:
                raise Exception("Fixer not initialized")

            card = data.get("card")
            additional_info = data.get("additional_info", "")

            if not card:
                raise Exception("card is required")

            # Build a minimal card structure for process_card_batch
            fields = {}
            for field_name, value in (card.get("original_fields") or {}).items():
                if isinstance(value, dict):
                    fields[field_name] = value
                else:
                    fields[field_name] = {"value": value, "order": 0}

            fake_card = {
                "note": {
                    "noteId": card.get("note_id"),
                    "modelName": card.get("model_name", "Basic"),
                    "fields": fields,
                    "tags": card.get("tags", []),
                }
            }

            processed_cards, raw_response = self.fixer.processor.process_card_batch(
                [fake_card], additional_info=additional_info
            )

            if processed_cards:
                result = {
                    "processed_card": processed_cards[0],
                    "raw_response": raw_response,
                }
            else:
                result = {"error": "No card returned from processing"}

            self.send_json_response(result)

        except Exception as e:
            print(f"Error in handle_retry_request: {e}")
            traceback.print_exc()
            self.send_json_error(500, str(e))

    def send_json_error(self, status_code: int, message: str):
        """Send a JSON error response"""
        response_data = json.dumps({"error": message}, ensure_ascii=False, indent=2)
        response_bytes = response_data.encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def send_json_response(self, data):
        """Send JSON response"""
        try:
            response_data = json.dumps(data, ensure_ascii=False, indent=2)

            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            response_bytes = response_data.encode("utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

        except Exception as e:
            print(f"Error serializing JSON response: {e}")
            print(f"Data type: {type(data)}")
            print(f"Data preview: {str(data)[:500]}")
            traceback.print_exc()

            # Send error response
            error_response = json.dumps(
                {"error": f"JSON serialization failed: {str(e)}"}
            )
            self.send_response(500)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(error_response)))
            self.end_headers()
            self.wfile.write(error_response.encode("utf-8"))

    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        """Override to reduce log noise"""
        if not self.path.startswith("/api/"):
            return
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] {self.command} {self.path} - {format % args}"
        )

    def get_interface_html(self):
        """Get the HTML interface content"""
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Anki Card Fixer</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #232728; min-height: 100vh; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; background: white; border-radius: 15px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); overflow: hidden; }
        .header { background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%); color: white; padding: 30px; text-align: center; }
        .header h1 { font-size: 2.5rem; margin-bottom: 10px; font-weight: 300; }
        .header p { opacity: 0.9; font-size: 1.1rem; }
        .controls { padding: 20px 30px; background: #f8f9fa; border-bottom: 1px solid #e9ecef; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; }
        .control-group { display: flex; align-items: center; gap: 15px; }
        .btn { padding: 12px 24px; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500; transition: all 0.3s ease; text-decoration: none; display: inline-flex; align-items: center; gap: 8px; }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .btn-primary { background: linear-gradient(135deg, #5d8764 0%, #2f522a 100%); color: white; }
        .btn-primary:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4); }
        .btn-secondary { background: #6c757d; color: white; }
        .btn-success { background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; }
        .btn-danger { background: linear-gradient(135deg, #dc3545 0%, #fd7e14 100%); color: white; }
        .main-content { padding: 30px; width: 100%; }
        .deck-selector { background: #f8f9fa; padding: 5px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; font-weight: 600; color: #495057; }
        .form-control { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; }
        .form-control:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); }
        .status-indicator { padding: 8px 16px; border-radius: 20px; font-size: 12px; font-weight: 600; text-transform: uppercase; }
        .status-connected { background: #d4edda; color: #155724; }
        .status-disconnected { background: #f8d7da; color: #721c24; }
        .processing { display: none; text-align: center; padding: 40px; }
        .processing-spinner { width: 40px; height: 40px; border: 4px solid #f3f3f3; border-top: 4px solid #667eea; border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 20px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .card { background: white; border: 1px solid #e9ecef; border-radius: 12px; margin-bottom: 20px; overflow: hidden; transition: all 0.3s ease; box-shadow: 0 2px 10px rgba(0,0,0,0.05); position: relative; }
        .card:hover { box-shadow: 0 5px 20px rgba(0,0,0,0.1); }
        .card.selected { border-color: #667eea; box-shadow: 0 5px 20px rgba(102, 126, 234, 0.2); }
        .card-header { background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); padding: 8px; border-bottom: 1px solid #e9ecef; display: flex; align-items: center; justify-content: space-between; }
        .card-title { font-size: 1.2rem; font-weight: 600; color: #2c3e50; display: flex; align-items: center; gap: 15px; }
        .checkbox-wrapper { display: flex; align-items: center; gap: 10px; }
        .custom-checkbox { width: 20px; height: 20px; border: 2px solid #ddd; border-radius: 4px; cursor: pointer; transition: all 0.3s ease; display: flex; align-items: center; justify-content: center; }
        .custom-checkbox.checked { background: #667eea; border-color: #667eea; color: white; }
        .card-body { padding: 0; }
        .field-group { border-bottom: 1px solid #f1f3f4; padding: 5px; }
        .field-group:last-child { border-bottom: none; }
        .field-label { font-weight: 600; color: #495057; margin-bottom: 10px; display: block; }
        .field-comparison { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .field-section { background: #f8f9fa; border-radius: 8px; padding: 5px; }
        .field-section h4 { color: #6c757d; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; }
        .field-content { overflow-y: auto; font-family: 'Consolas', 'Monaco', monospace; font-size: 14px; line-height: 1.5; word-break: break-word; }
        .field-input { width: 100%; min-height: 220px; padding: 5px; border: 1px solid #ddd; border-radius: 8px; font-family: inherit; font-size: 14px; resize: vertical; transition: border-color 0.3s ease; }
        .field-input:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); }
        .field-input-front { width: 100%; min-height: 40px; padding: 5px; border: 1px solid #ddd; border-radius: 8px; font-family: inherit; font-size: 14px; resize: vertical; transition: border-color 0.3s ease; }
        .field-input-front:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); }
        .field-input-back { width: 100%; min-height: 110px; padding: 5px; border: 1px solid #ddd; border-radius: 8px; font-family: inherit; font-size: 14px; resize: vertical; transition: border-color 0.3s ease; }
        .field-input-back:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); }
        .changes-list { background: #e8f4f8; border-left: 4px solid #17a2b8; padding: 5px; padding-left: 25px; margin-top: 15px; border-radius: 0 8px 8px 0; }
        .stats { display: flex; gap: 20px; align-items: center; font-weight: 500; color: #495057; }
        .stat-item { display: flex; align-items: center; gap: 8px; }
        .empty-state { text-align: center; padding: 60px 20px; color: #6c757d; }
        .diff-container { font-family: 'Consolas', 'Monaco', monospace; font-size: 14px; line-height: 1.5; }
        .diff-split { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; border: 1px solid #ddd; border-radius: 4px; overflow: hidden; /* max-height: 300px; */ }
        .diff-left, .diff-right { background: #f8f9fa; display: flex; flex-direction: column; min-height: 0; }
        .diff-header { background: #e9ecef; padding: 5px; font-weight: 600; font-size: 12px; color: #495057; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #ddd; flex-shrink: 0; }
        .diff-content { padding: 8px; font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; line-height: 1.4; white-space: pre-wrap; word-wrap: break-word; overflow-y: auto; flex: 1; }
        .diff-added { background-color: #d4edda; color: #155724; text-decoration: none; padding: 2px 4px; border-radius: 2px; }
        .diff-removed { background-color: #f8d7da; color: #721c24; text-decoration: line-through; padding: 2px 4px; border-radius: 2px; }
        .diff-unchanged { color: #6c757d; }
        @media (max-width: 768px) { .diff-split { grid-template-columns: 1fr; } .diff-header { text-align: center; } }
        .field-preview { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 15px; margin-top: 10px; }
        .field-preview h4 { color: #495057; font-size: 0.9rem; margin-bottom: 10px; }
        .preview-content { font-size: 14px; line-height: 1.5; color: white; background-color: #363636; padding: 10px; border-radius: 4px; }
        .field-tabs { border-bottom: 1px solid #ddd; display: flex; background: #f8f9fa; border-radius: 8px 8px 0 0; }
        .field-tab { padding: 8px 16px; cursor: pointer; border: none; background: none; color: #6c757d; font-size: 0.85rem; font-weight: 500; transition: all 0.3s ease; flex: 1; text-align: center; }
        .field-tab:first-child { border-radius: 8px 0 0 0; }
        .field-tab:last-child { border-radius: 0 8px 0 0; }
        .field-tab.active { background: white; color: #495057; border-bottom: 2px solid #667eea; }
        .field-tab:hover:not(.active) { background: #e9ecef; }
        .tab-content { display: none; padding: 5px; }
        .tab-content.active { display: block; }
        .field-section-tabbed { background: #f8f9fa; border-radius: 0 0 8px 8px; border: 1px solid #ddd; }
        .reference-links { background: #f8f9fa; border: 1px solid #ddd; border-radius: 8px; padding: 15px; margin-top: 10px; display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
        .reference-links h4 { color: #495057; font-size: 0.9rem; margin-bottom: 0; }
        .reference-links a { display: inline-block; padding: 6px 12px; background: #e9ecef; color: #495057; text-decoration: none; border-radius: 6px; font-size: 0.85rem; transition: all 0.3s ease; }
        .reference-links a:hover { background: #667eea; color: white; transform: translateY(-1px); }
        .btn-toggle-retry { margin-left: auto; padding: 6px 12px; background: #e9ecef; color: #495057; border: none; border-radius: 6px; font-size: 0.85rem; cursor: pointer; transition: all 0.3s ease; display: inline-flex; align-items: center; gap: 6px; }
        .btn-toggle-retry:hover { background: #667eea; color: white; transform: translateY(-1px); }
        .btn-toggle-retry.active { background: #667eea; color: white; }
        .btn-toggle-retry .arrow { display: inline-block; transition: transform 0.3s ease; font-size: 0.7rem; }
        .btn-toggle-retry.active .arrow { transform: rotate(90deg); }
        @media (max-width: 768px) { .field-comparison { grid-template-columns: 1fr; } }
        .retry-section { background: #f0f4ff; border-top: 1px solid #ddd; padding: 12px 15px; display: none; gap: 10px; align-items: flex-end; }
        .retry-section.visible { display: flex; }
        .retry-section textarea { flex: 1; min-height: 36px; max-height: 120px; padding: 8px; border: 1px solid #c5cae9; border-radius: 6px; font-family: inherit; font-size: 13px; resize: vertical; }
        .retry-section textarea:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); }
        .retry-section .btn-retry { padding: 8px 18px; white-space: nowrap; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500; transition: all 0.3s ease; }
        .retry-section .btn-retry:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 3px 10px rgba(102, 126, 234, 0.4); }
        .retry-section .btn-retry:disabled { opacity: 0.6; cursor: not-allowed; }
        .card-loading-overlay { position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(255,255,255,0.85); display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 10; border-radius: 12px; }
        .card-loading-overlay .processing-spinner { width: 30px; height: 30px; border: 3px solid #f3f3f3; border-top: 3px solid #667eea; border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 10px; }
        .card-loading-overlay p { color: #495057; font-size: 14px; font-weight: 500; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Anki Card Fixer</h1>
        </div>

        <div class="controls" id="mainControls">
        <div class="controls" id="mainControls">
            <div class="control-group">
                <div class="status-indicator" id="statusIndicator">Connecting...</div>
            </div>
            <div class="stats" id="statsDisplay" style="display: none;">
                <div class="stat-item"><span>✅</span><span>Selected: <span id="selectedCards">0</span>/<span id="totalCards">0</span></span></div>
            </div>
            <div class="control-group" id="actionControls" style="display: none;">
                <button class="btn btn-secondary" onclick="selectAll()" id="selectAllBtn">Select All</button>
                <button class="btn btn-secondary" onclick="selectNone()" id="selectNoneBtn">Select None</button>
                <button class="btn btn-success" onclick="applyChanges()" disabled id="applyBtn">Apply Changes</button>
            </div>
        </div>

        <div class="main-content">
            <div class="deck-selector" id="deckSelector">
                <div style="display: grid; grid-template-columns: 1fr; gap: 15px;">
                    <div class="form-group">
                        <label for="deckSelect">Select Deck:</label>
                        <select id="deckSelect" class="form-control"></select>
                    </div>
                </div>
                <div style="display: grid; grid-template-columns: 1fr; gap: 15px; border: 1px solid #ddd; border-radius: 12px; padding: 10px; margin-top: 15px; background-color: #deeffd;">
                    <div class="form-group">
                        <label for="batchSize">Batch Size:</label>
                        <input type="number" id="batchSize" class="form-control" value="10" min="1" max="500">
                    </div>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; border: 1px solid #ddd; border-radius: 12px; padding: 10px; margin-top: 15px;     background-color: #f5edeb;">
                    <div class="form-group">
                        <label for="wordList">Word list (comma-separated):</label>
                        <input type="text" id="wordList" class="form-control">
                    </div>
                    <div class="form-group">
                        <label for="flaggedOnly">Flagged only:</label>
                        <input type="checkbox" id="flaggedOnly">
                    </div>
                    <div style="display: flex; align-items: center;">
                        <label><input type="checkbox" id="createBackup"> Create backup</label>
                    </div>
                </div>
                <div style="display: grid; grid-template-columns: 1fr; gap: 15px; margin-top: 15px;">
                    <div class="form-group">
                        <label>&nbsp;</label>
                        <button class="btn btn-primary" onclick="processCards()" id="processBtn" style="width: 100%;">Fix Cards</button>
                    </div>
                </div>
            </div>

            <div class="processing" id="processing">
                <div class="processing-spinner"></div>
                <p id="processingText">Processing cards...</p>
            </div>

            <div id="cardContainer" style="display: none;">
                <!-- Cards will be generated here -->
            </div>

            <div class="skipped-cards-warning" id="skippedCardsWarning" style="display: none;">
                <div style="background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 8px; margin: 20px 0; padding: 20px;">
                    <div style="display: flex; align-items: center; margin-bottom: 15px;">
                        <span style="font-size: 1.5rem; margin-right: 10px;">⚠️</span>
                        <h3 style="margin: 0; color: #856404;">Skipped Cards Warning</h3>
                    </div>
                    <p style="margin: 0 0 15px 0; color: #856404;">Some cards were skipped during processing. Please review the details below:</p>
                    <div id="skippedCardsList" style="background: #fff; border: 1px solid #ffeaa7; border-radius: 6px; padding: 15px;">
                        <!-- Skipped cards will be listed here -->
                    </div>
                </div>
            </div>

            <div class="empty-state" id="emptyState" style="display: none;">
                <div style="font-size: 4rem; margin-bottom: 20px; opacity: 0.5;">📝</div>
                <h3>No cards to review</h3>
                <p>Select a deck and click "Fix Cards" to get started</p>
            </div>

            <div class="debug-section" id="debugSection" style="display: none; margin-top: 30px;">
                <div class="debug-header" onclick="toggleDebugOutput()" style="background: #f8f9fa; border: 1px solid #ddd; border-radius: 8px 8px 0 0; padding: 15px; cursor: pointer; display: flex; justify-content: space-between; align-items: center;">
                    <h3 style="margin: 0; color: #495057;">🔍 Raw Model Output (Debug)</h3>
                    <span id="debugToggle" style="color: #6c757d;">▼ Show</span>
                </div>
                <div class="debug-content" id="debugContent" style="display: none; border: 1px solid #ddd; border-top: none; border-radius: 0 0 8px 8px; padding: 20px; background: #f8f9fa;">
                    <pre id="rawClaudeOutput" style="background: white; border: 1px solid #ddd; border-radius: 4px; padding: 15px; font-family: 'Consolas', 'Monaco', monospace; font-size: 12px; line-height: 1.4; white-space: pre-wrap; word-wrap: break-word; max-height: 400px; overflow-y: auto; margin: 0;"></pre>
                </div>
            </div>
        </div>
    </div>

    <script>
        let cardData = [];
        let selectedCards = new Set();
        let skippedCards = [];
        let currentDeckName = '';

        document.addEventListener('DOMContentLoaded', function() {
            checkServerStatus();
            loadDecks();
            window.addEventListener('beforeunload', handleBeforeUnload);
        });

        async function checkServerStatus() {
            try {
                const response = await fetch('/api/status');
                const status = await response.json();
                
                const indicator = document.getElementById('statusIndicator');
                if (status.anki_connected && status.claude_api) {
                    console.log("Server connected and ready");
                    indicator.textContent = 'Connected';
                    indicator.className = 'status-indicator status-connected';
                } else {
                    console.log("Server not fully connected");
                    indicator.textContent = 'Disconnected';
                    indicator.className = 'status-indicator status-disconnected';
                }
            } catch (error) {
                console.error('Error checking status:', error);
                const indicator = document.getElementById('statusIndicator');
                indicator.textContent = 'Error';
                indicator.className = 'status-indicator status-disconnected';
            }
        }

        async function loadDecks() {
            try {
                const response = await fetch('/api/decks');
                const responseText = await response.text();

                let data;
                try {
                    data = JSON.parse(responseText);
                } catch (parseError) {
                    console.error("JSON parse error:", parseError);
                    console.error("Failed to parse response:", responseText);
                    throw new Error('Unexpected response from server');
                }

                if (!response.ok) {
                    const errorMessage = (data && data.error) ? data.error : `HTTP ${response.status}`;
                    throw new Error(errorMessage);
                }
                
                const deckSelect = document.getElementById('deckSelect');
                deckSelect.innerHTML = '<option value="">Select a deck...</option>';
                
                data.decks.forEach(deck => {
                    const option = document.createElement('option');
                    option.value = deck;
                    option.textContent = deck;
                    deckSelect.appendChild(option);
                });
                
                if (data.decks.includes('Default')) {
                    deckSelect.value = 'Default';
                }
            } catch (error) {
                console.error('Error loading decks:', error);
                const message = (error && error.message) ? error.message : String(error);
                if (message.includes('Cannot connect to Anki')) {
                    alert('Cannot connect to Anki. Make sure Anki is running with AnkiConnect add-on installed.');
                } else {
                    alert(message);
                }
            }
        }

        async function processCards() {
            const deckName = document.getElementById('deckSelect').value;
            const batchSize = parseInt(document.getElementById('batchSize').value);
            const createBackup = document.getElementById('createBackup').checked;
            const wordList = document.getElementById('wordList').value.trim();
            const flaggedOnly = document.getElementById('flaggedOnly').checked;
            
            if (!deckName) {
                alert('Please select a deck');
                return;
            }

            currentDeckName = deckName;
            showProcessing();

            try {
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 240000); // 240 second timeout
                
                const response = await fetch('/api/process', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        deck_name: deckName,
                        batch_size: batchSize,
                        start_from: 0,
                        create_backup: createBackup,
                        word_list: wordList,
                        flagged_only: flaggedOnly
                    }),
                    signal: controller.signal
                });
                
                clearTimeout(timeoutId);

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
                }

                const responseText = await response.text();
                
                let data;
                try {
                    data = JSON.parse(responseText);
                } catch (parseError) {
                    console.error("JSON parse error:", parseError);
                    console.error("Failed to parse response:", responseText);
                    throw new Error(`Invalid JSON response: ${parseError.message}`);
                }
                loadCardData(data);
                hideProcessing();
                showResults();
                
            } catch (error) {
                console.error('Error processing cards:', error);
                alert('Error processing cards: ' + error.message);
                hideProcessing();
            }
        }

        async function applyChanges() {
            if (selectedCards.size === 0) {
                alert('No cards selected');
                return;
            }

            const selectedCardData = Array.from(selectedCards).map(index => cardData[index]);
            
            showProcessing('Applying changes...');

            try {
                const response = await fetch('/api/apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        cards: selectedCardData,
                        deck_name: currentDeckName
                    })
                });

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
                }

                const result = await response.json();
                hideProcessing();
                selectedCards.clear();
                cardData = [];
                updateStats();

            } catch (error) {
                console.error('Error applying changes:', error);
                alert('Error applying changes: ' + error.message);
                hideProcessing();
            }
        }

        function showProcessing(text = 'Processing cards...') {
            document.getElementById('processingText').textContent = text;
            document.getElementById('deckSelector').style.display = 'none';
            document.getElementById('processing').style.display = 'block';
            document.getElementById('cardContainer').style.display = 'none';
            document.getElementById('actionControls').style.display = 'none';
        }

        function hideProcessing() {
            document.getElementById('processing').style.display = 'none';
            document.getElementById('deckSelector').style.display = 'block';
        }

        function showResults() {
            document.getElementById('cardContainer').style.display = 'block';
            document.getElementById('actionControls').style.display = 'flex';
            document.getElementById('statsDisplay').style.display = 'flex';
        }

        function loadCardData(data) {
            cardData = data.processed_cards || [];
            full_log = data.full_log || '';
            skippedCards = data.skipped_cards || [];
            selectedCards.clear();
            cardData.forEach((_, index) => {
                selectedCards.add(index);
            });
            renderCards();
            renderSkippedCards();
            updateStats();
            
            // Show debug section and populate raw output
            document.getElementById('debugSection').style.display = 'block';
            document.getElementById('rawClaudeOutput').textContent = full_log;
        }

        function renderCards() {
            const container = document.getElementById('cardContainer');
            container.innerHTML = '';

            if (cardData.length === 0) {
                document.getElementById('emptyState').style.display = 'block';
                return;
            }

            document.getElementById('emptyState').style.display = 'none';

            cardData.forEach((card, index) => {
                const cardElement = createCardElement(card, index);
                container.appendChild(cardElement);
            });
        }

        function renderSkippedCards() {
            const warningContainer = document.getElementById('skippedCardsWarning');
            const skippedCardsList = document.getElementById('skippedCardsList');
            
            if (!skippedCards || skippedCards.length === 0) {
                warningContainer.style.display = 'none';
                return;
            }
            
            warningContainer.style.display = 'block';
            skippedCardsList.innerHTML = '';
            
            skippedCards.forEach((skippedCard, index) => {
                const skippedItem = document.createElement('div');
                skippedItem.style.cssText = 'margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid #ffeaa7;';
                
                if (index === skippedCards.length - 1) {
                    skippedItem.style.borderBottom = 'none';
                    skippedItem.style.marginBottom = '0';
                    skippedItem.style.paddingBottom = '0';
                }
                
                let title = '';
                let icon = '';
                
                if (skippedCard.reason === 'multiple_matches') {
                    title = `Word: "${skippedCard.word}"`;
                    icon = '🔍';
                } else if (skippedCard.reason === 'missing_note') {
                    title = `Card ID: ${skippedCard.card_id}`;
                    icon = '🚫';
                } else {
                    title = 'Unknown item';
                    icon = '❓';
                }
                
                skippedItem.innerHTML = `
                    <div style="display: flex; align-items: flex-start; margin-bottom: 8px;">
                        <span style="font-size: 1.2rem; margin-right: 8px; margin-top: 2px;">${icon}</span>
                        <div style="flex: 1;">
                            <strong style="color: #856404;">${title}</strong>
                            <div style="color: #856404; font-size: 0.9rem; margin-top: 4px;">${skippedCard.details}</div>
                        </div>
                    </div>
                `;
                
                skippedCardsList.appendChild(skippedItem);
            });
        }

        function createCardElement(card, index) {
            const cardDiv = document.createElement('div');
            cardDiv.className = 'card';
            cardDiv.id = `card-${index}`;

            const isSelected = selectedCards.has(index);
            
            if (isSelected) {
                cardDiv.classList.add('selected');
            }

            cardDiv.innerHTML = `
                <div class="card-header">
                    <div class="card-title">
                        <div class="checkbox-wrapper">
                            <div class="custom-checkbox ${isSelected ? 'checked' : ''}" onclick="toggleCard(${index})">
                                ${isSelected ? '✓' : ''}
                            </div>
                        </div>
                        Card ${index + 1}: ${getCardTitle(card)}
                    </div>
                </div>
                <div class="card-body">
                    ${renderFields(card, index)}
                    ${renderReferences(card, index)}
                </div>
                <div class="retry-section" id="retry-section-${index}">
                    <textarea id="retry-info-${index}" placeholder="Additional instructions for regeneration (e.g. 'include these definitions: ...')"></textarea>
                    <button class="btn-retry" id="retry-btn-${index}" onclick="retryCard(${index})">Regenerate</button>
                </div>
            `;

            return cardDiv;
        }

        function getCardTitle(card) {
            const fields = card.updated_fields || {};
            const originalFields = card.original_fields || {};
            
            // Try to get front field from updated fields first, then original
            let front = fields.Front || fields.front || '';
            if (!front) {
                const originalFront = originalFields.Front || originalFields.front;
                front = originalFront && typeof originalFront === 'object' ? originalFront.value : (originalFront || '');
            }
            
            if (front) {
                const cleanFront = front.replace(/<[^>]*>/g, '').trim();
                return cleanFront.length > 50 ? cleanFront.substring(0, 50) + '...' : cleanFront;
            }
            return `Note ID: ${card.note_id || 'Unknown'}`;
        }

        function renderFields(card, cardIndex) {
            const fields = card.updated_fields || {};
            const originalFields = card.original_fields || {};
            
            let fieldsHtml = '';
            
            Object.keys(fields).forEach(fieldName => {
                const newValue = fields[fieldName];
                // Anki fields are objects with a 'value' property
                const oldValueObj = originalFields[fieldName];
                const oldValue = oldValueObj && typeof oldValueObj === 'object' ? oldValueObj.value : (oldValueObj || '');
                
                // Check if there are actual changes
                const hasChanges = oldValue !== newValue;
                
                // Front field is always shown as an editable textarea
                if (fieldName === 'Front') {
                    const displayValue = hasChanges ? newValue : oldValue;
                    const changeLabel = hasChanges ? '' : ' <span style="color: #6c757d; font-weight: normal;">(no changes)</span>';
                    fieldsHtml += `
                        <div class="field-group">
                            <label class="field-label">${fieldName}${changeLabel}</label>
                            <div class="field-section" style="border: 1px solid #ddd; border-radius: 8px;">
                                <textarea class="field-input-front" 
                                         onchange="updateField(${cardIndex}, '${fieldName}', this.value)"
                                         oninput="updateFieldAndRefresh(${cardIndex}, '${fieldName}', this.value, 'no-changes-${cardIndex}-${fieldName}')"
                                         placeholder="Enter ${fieldName} content...">${escapeHtml(displayValue) || ''}</textarea>
                            </div>
                        </div>
                    `;
                } else if (!hasChanges) {
                    fieldsHtml += `
                        <div class="field-group">
                            <label class="field-label">${fieldName} <span style="color: #6c757d; font-weight: normal;">(no changes)</span></label>
                            <div class="field-section" style="border: 1px solid #ddd; border-radius: 8px;">
                                <div class="field-content" style="padding: 15px;">${escapeHtml(oldValue) || '<em>Empty</em>'}</div>
                            </div>
                        </div>
                    `;
                } else {
                    // Has changes - show full tabbed interface
                    const diffHtml = generateDiff(oldValue, newValue);
                    
                    // Generate HTML preview for Back field and reference links
                    let previewHtml = '';
                    if (newValue) {
                        // Replace newlines with <br> for HTML preview
                        const previewValue = newValue.replace(/\\n/g, '<br>');
                        
                        previewHtml = `
                            <div class="field-preview">
                                <h4>Preview</h4>
                                <div class="preview-content">${previewValue}</div>
                            </div>
                        `;
                    }
                    
                    const tabId = `field-${cardIndex}-${fieldName.replace(/\\s+/g, '')}`;
                    
                    const inputClass = fieldName === 'Back' ? 'field-input-back' : 'field-input';
                    
                    fieldsHtml += `
                        <div class="field-group">
                            <label class="field-label">${fieldName}</label>
                            <div class="field-section-tabbed">
                                <div class="field-tabs">
                                    <button class="field-tab" onclick="switchTab('${tabId}', 'previous', this)">Previous</button>
                                    <button class="field-tab active" onclick="switchTab('${tabId}', 'diff', this)">Diff</button>
                                    <button class="field-tab" onclick="switchTab('${tabId}', 'updated', this)">Updated</button>
                                </div>
                                <div id="${tabId}-previous" class="tab-content">
                                    <div class="field-content">${escapeHtml(oldValue) || '<em>Empty</em>'}</div>
                                </div>
                                <div id="${tabId}-diff" class="tab-content active">
                                    <div class="field-content diff-container">${diffHtml}</div>
                                    ${previewHtml}
                                </div>
                                <div id="${tabId}-updated" class="tab-content">
                                    <textarea class="${inputClass}" 
                                             onchange="updateField(${cardIndex}, '${fieldName}', this.value)"
                                             oninput="updateFieldAndRefresh(${cardIndex}, '${fieldName}', this.value, '${tabId}')"
                                             onkeydown="handleTextareaKeydown(event, ${cardIndex}, '${fieldName}', '${tabId}')"
                                             placeholder="Enter ${fieldName} content...">${escapeHtml(newValue)}</textarea>
                                    ${previewHtml}
                                </div>
                            </div>
                        </div>
                    `;
                }
            });
            
            return fieldsHtml;
        }
        
        function renderReferences(card, cardIndex) {
                // Extract Swedish word for reference links
                const swedishWord = extractSwedishWord(card, cardIndex);
                let referencesHtml = '';
                if (swedishWord) {
                    const wiktionaryUrl = `https://sv.wiktionary.org/wiki/${encodeURIComponent(swedishWord)}`;
                    const reversoUrl = `https://context.reverso.net/översättning/svenska-engelska/${encodeURIComponent(swedishWord)}`;
                    const synonymerUrl = `https://www.synonymer.se/sv-syn/${encodeURIComponent(swedishWord)}`;

                    referencesHtml = `
                        <div class="reference-links">
                            <a href="${wiktionaryUrl}" target="_blank" rel="noopener">📚 Wiktionary</a>
                            <a href="${reversoUrl}" target="_blank" rel="noopener">🔄 Reverso Context</a>
                            <a href="${synonymerUrl}" target="_blank" rel="noopener">🔣 Synonymer</a>
                            <button class="btn-toggle-retry" onclick="toggleRetry(${cardIndex})" id="toggle-retry-btn-${cardIndex}">Regenerate <span class="arrow">&#9654;</span></button>
                        </div>
                    `;
                }
                
                return referencesHtml;
        }

        function toggleCard(index) {
            const checkbox = document.querySelector(`#card-${index} .custom-checkbox`);
            const card = document.getElementById(`card-${index}`);
            
            if (selectedCards.has(index)) {
                selectedCards.delete(index);
                checkbox.classList.remove('checked');
                checkbox.textContent = '';
                card.classList.remove('selected');
            } else {
                selectedCards.add(index);
                checkbox.classList.add('checked');
                checkbox.textContent = '✓';
                card.classList.add('selected');
            }
            
            updateStats();
        }

        function selectAll() {
            cardData.forEach((_, index) => {
                if (!selectedCards.has(index)) {
                    selectedCards.add(index);
                    const checkbox = document.querySelector(`#card-${index} .custom-checkbox`);
                    const card = document.getElementById(`card-${index}`);
                    if (checkbox && card) {
                        checkbox.classList.add('checked');
                        checkbox.textContent = '✓';
                        card.classList.add('selected');
                    }
                }
            });
            updateStats();
        }

        function selectNone() {
            selectedCards.clear();
            cardData.forEach((_, index) => {
                const checkbox = document.querySelector(`#card-${index} .custom-checkbox`);
                const card = document.getElementById(`card-${index}`);
                if (checkbox && card) {
                    checkbox.classList.remove('checked');
                    checkbox.textContent = '';
                    card.classList.remove('selected');
                }
            });
            updateStats();
        }

        function updateField(cardIndex, fieldName, newValue) {
            if (cardData[cardIndex] && cardData[cardIndex].updated_fields) {
                cardData[cardIndex].updated_fields[fieldName] = newValue;
            }
        }

        function updateFieldAndRefresh(cardIndex, fieldName, newValue, tabId) {
            // Update the field data
            updateField(cardIndex, fieldName, newValue);
            
            // Get original value for comparison
            const card = cardData[cardIndex];
            const originalFields = card.original_fields || {};
            const oldValueObj = originalFields[fieldName];
            const oldValue = oldValueObj && typeof oldValueObj === 'object' ? oldValueObj.value : (oldValueObj || '');
            
            // Update diff view
            const diffContainer = document.getElementById(`${tabId}-diff`).querySelector('.field-content');
            if (diffContainer) {
                diffContainer.innerHTML = generateDiff(oldValue, newValue);
            }
            
            // Update HTML preview if this is the Back field
            if (fieldName === 'Back' && newValue) {
                const previewValue = newValue.replace(/\\n/g, '<br>');
                const previewContainer = document.querySelector(`#${tabId}-updated .preview-content`);
                if (previewContainer) {
                    previewContainer.innerHTML = previewValue;
                }
                const previewContainer2 = document.querySelector(`#${tabId}-diff .preview-content`);
                if (previewContainer2) {
                    previewContainer2.innerHTML = previewValue;
                }
            }
        }

        function handleTextareaKeydown(event, cardIndex, fieldName, tabId) {
            if (!event.shiftKey && (event.ctrlKey || event.metaKey) && (event.key === 'i' || event.key === 'I')) {
                const textarea = event.target;
                const start = textarea.selectionStart;
                const end = textarea.selectionEnd;
                if (start == null || end == null) {
                    return;
                }
                event.preventDefault();
                if (start === end) {
                    return; // do nothing if no selection
                }
                const value = textarea.value;
                let before = value.slice(0, start);
                let selected = value.slice(start, end);
                let after = value.slice(end);
                const trailingWhitespaceMatch = selected.match(/(\\s+)$/);
                if (trailingWhitespaceMatch) {
                    const trailingWhitespace = trailingWhitespaceMatch[1];
                    selected = selected.slice(0, -trailingWhitespace.length);
                    after = trailingWhitespace + after;
                }
                if (selected.length === 0) {
                    return;
                }
                const wrapped = '<i>' + selected + '</i>';
                textarea.value = before + wrapped + after;
                const newCursorPos = start + wrapped.length; // position after </i>
                textarea.setSelectionRange(newCursorPos, newCursorPos);
                updateFieldAndRefresh(cardIndex, fieldName, textarea.value, tabId);
            }
        }

        function updateStats() {
            document.getElementById('totalCards').textContent = cardData.length;
            document.getElementById('selectedCards').textContent = selectedCards.size;
            
            const applyBtn = document.getElementById('applyBtn');
            applyBtn.disabled = selectedCards.size === 0;

            const selectAllBtn = document.getElementById('selectAllBtn');
            if (selectAllBtn) {
                selectAllBtn.disabled = cardData.length === 0 || selectedCards.size === cardData.length;
            }

            const selectNoneBtn = document.getElementById('selectNoneBtn');
            if (selectNoneBtn) {
                selectNoneBtn.disabled = cardData.length === 0 || selectedCards.size === 0;
            }
        }

        function extractSwedishWord(card, cardIndex) {
            // Try to extract the main Swedish word from Front field
            const frontField = card.updated_fields?.Front || 
                              (card.original_fields?.Front && typeof card.original_fields.Front === 'object' ? 
                               card.original_fields.Front.value : card.original_fields?.Front) || '';
            console.log(`Extracting Swedish word from card ${cardIndex + 1}:`, frontField);
            if (!frontField) return null;
            
            // Remove HTML tags
            let cleanText = frontField.replace(/<[^>]*>/g, '');
            
            // Remove articles (en, ett, den, det) from the beginning
            cleanText = cleanText.replace(/^(en|ett|den|det|att)\\s+/i, '');
            
            // Remove parentheses and their contents (like counts or grammar info)
            cleanText = cleanText.replace(/\\([^)]*\\)/g, '');

            cleanText = cleanText.split(/\\s+[\\-–—:]\\s+/)[0];
            cleanText = cleanText.replace(/\\s+/g, ' ').trim();
            cleanText = cleanText.replace(/[\\.,;:!?]+$/g, '').trim();

            return cleanText ? cleanText.toLowerCase() : null;
        }

        function generateDiff(oldText, newText) {
            if (!oldText && !newText) return '<div class="diff-split"><div class="diff-left"><em>No content</em></div><div class="diff-right"><em>No content</em></div></div>';
            
            // Split view with highlighting
            const oldHighlighted = highlightDifferences(oldText, newText, 'removed');
            const newHighlighted = highlightDifferences(newText, oldText, 'added');
            
            return `
                <div class="diff-split">
                    <div class="diff-left">
                        <div class="diff-header">Previous</div>
                        <div class="diff-content">${oldHighlighted || '<em>Empty</em>'}</div>
                    </div>
                    <div class="diff-right">
                        <div class="diff-header">Updated</div>
                        <div class="diff-content">${newHighlighted || '<em>Empty</em>'}</div>
                    </div>
                </div>
            `;
        }

        function highlightDifferences(text, compareText, className) {
            if (!text) return '';
            if (!compareText) return `<span class="diff-${className}">${escapeHtml(text)}</span>`;
            if (text === compareText) return escapeHtml(text);
            
            // Simple word-based highlighting
            const words = text.split(/(\\s+)/);
            const compareWords = compareText.split(/(\\s+)/);
            
            return words.map(word => {
                if (word.trim() === '') return word; // Keep whitespace as-is
                
                // Check if word exists in the other text
                if (compareWords.includes(word)) {
                    return escapeHtml(word);
                } else {
                    return `<span class="diff-${className}">${escapeHtml(word)}</span>`;
                }
            }).join('');
        }

        function switchTab(tabId, tabName, buttonElement) {
            // Hide all tab contents for this field
            const tabContents = document.querySelectorAll(`[id^="${tabId}-"]`);
            tabContents.forEach(content => {
                content.classList.remove('active');
            });
            
            // Remove active class from all tab buttons for this field
            const tabButtons = buttonElement.parentElement.querySelectorAll('.field-tab');
            tabButtons.forEach(button => {
                button.classList.remove('active');
            });
            
            // Show the selected tab content
            document.getElementById(`${tabId}-${tabName}`).classList.add('active');
            
            // Add active class to the clicked button
            buttonElement.classList.add('active');
        }

        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function toggleDebugOutput() {
            const content = document.getElementById('debugContent');
            const toggle = document.getElementById('debugToggle');
            
            if (content.style.display === 'none') {
                content.style.display = 'block';
                toggle.textContent = '▲ Hide';
            } else {
                content.style.display = 'none';
                toggle.textContent = '▼ Show';
            }
        }

        function handleBeforeUnload(e) {
            if (hasUnsavedWork()) {
                e.preventDefault();
                // Most browsers ignore the custom message and show a generic prompt
                e.returnValue = '';
            }
        }

        function hasUnsavedWork() {
            return (cardData.length > 0);
        }

        function toggleRetry(index) {
            const section = document.getElementById(`retry-section-${index}`);
            const btn = document.getElementById(`toggle-retry-btn-${index}`);
            if (section.classList.contains('visible')) {
                section.classList.remove('visible');
                btn.classList.remove('active');
            } else {
                section.classList.add('visible');
                btn.classList.add('active');
                document.getElementById(`retry-info-${index}`).focus();
            }
        }

        async function retryCard(index) {
            const card = cardData[index];
            if (!card) return;

            const additionalInfo = document.getElementById(`retry-info-${index}`).value.trim();
            const retryBtn = document.getElementById(`retry-btn-${index}`);
            const cardEl = document.getElementById(`card-${index}`);

            // Show loading overlay on the card
            retryBtn.disabled = true;
            retryBtn.textContent = 'Regenerating...';
            const overlay = document.createElement('div');
            overlay.className = 'card-loading-overlay';
            overlay.innerHTML = '<div class="processing-spinner"></div><p>Regenerating card...</p>';
            cardEl.appendChild(overlay);

            try {
                const response = await fetch('/api/retry', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        card: card,
                        additional_info: additionalInfo
                    })
                });

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
                }

                const result = await response.json();

                if (result.error) {
                    throw new Error(result.error);
                }

                // Update the card data in place, preserving original_fields
                const newCard = result.processed_card;
                newCard.original_fields = card.original_fields;
                newCard.note_id = card.note_id;
                newCard.model_name = card.model_name;
                newCard.tags = card.tags;
                cardData[index] = newCard;

                // Re-render just this card
                const wasSelected = selectedCards.has(index);
                const newCardEl = createCardElement(newCard, index);
                cardEl.replaceWith(newCardEl);

                // Restore selection state
                if (wasSelected && !selectedCards.has(index)) {
                    selectedCards.add(index);
                }
                updateStats();

            } catch (error) {
                console.error('Error retrying card:', error);
                alert('Error regenerating card: ' + error.message);
                // Remove overlay on error
                overlay.remove();
                retryBtn.disabled = false;
                retryBtn.textContent = 'Regenerate';
            }
        }
    </script>
</body>
</html>"""


def start_web_server(fixer, port: int = 8080):
    """Start the web server"""
    WebServer.fixer = fixer

    server = HTTPServer(("localhost", port), WebServer)

    print(f"🚀 Starting web server on http://localhost:{port}")
    print("🌐 Opening browser...")

    # Start server in a separate thread
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Open browser
    webbrowser.open(f"http://localhost:{port}")

    return server


offline_updates = [
    {
        "note": {
            "id": 1709631611485,
            "fields": {
                "Front": "En ättling",
                "Back": 'En avkomling eller släkting i nedstigande led (descendant)<br>("Hon var <i>ättling</i> till den berömda författaren")<br><span style="color: #C2C2C2">syn: avkomling, efterkomling, descendant</span>',
            },
            "tags": ["reviewed"],
        }
    },
    {
        "note": {
            "id": 1710707690534,
            "fields": {
                "Front": "En kvint",
                "Back": 'Det femte tonstegintervallet i en diatonisk skala<br>("En perfekt <i>kvint</i> är ett av de renaste intervallen")<br><span style="color: #C2C2C2">se även: prim, sekund, ters, kvart, sext, septima, oktav, nona</span>',
            },
            "tags": ["reviewed"],
        }
    }
]


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Fix Swedish Anki flashcards using Claude AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python anki_deck_fixer.py                    # Interactive mode with backup
  python anki_deck_fixer.py --no-backup       # Interactive mode without backup
  python anki_deck_fixer.py --deck "Swedish"  # Process specific deck
  python anki_deck_fixer.py --batch-size 5    # Smaller batches
  python anki_deck_fixer.py --start-from 100  # Start from card 100
  python anki_deck_fixer.py --web             # Start web interface

Environment Variables:
  ANTHROPIC_API_KEY   Required: Your Claude API key
  FORVO_API_KEY       Optional: Your Forvo API key for audio
        """,
    )

    parser.add_argument("--deck", type=str, help="Deck name to process")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of cards to process in each batch (default: 10)",
    )
    parser.add_argument(
        "--start-from",
        type=int,
        default=0,
        help="Card number to start from (1-based, default: 0)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip creating backup (not recommended)",
    )
    parser.add_argument(
        "--list-decks", action="store_true", help="List available decks and exit"
    )
    parser.add_argument("--web", action="store_true", help="Start web interface")
    parser.add_argument(
        "--port", type=int, default=8080, help="Port for web interface (default: 8080)"
    )
    parser.add_argument(
        "--word_list",
        type=str,
        help="Existing words in the deck separated by commas to modify",
    )
    parser.add_argument(
        "--parse_offline_updates", action="store_true", help="Parse offline updates"
    )
    parser.add_argument(
        "--flagged_only", action="store_true", help="Only process cards with flag 1 set"
    )

    return parser.parse_args()


class SwedishCardProcessor:
    """Processes Swedish flashcards using Claude API"""

    def __init__(
        self,
        api_key: str,
        forvo_api_key: Optional[str] = None,
        anki_connector: Optional[AnkiConnector] = None,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.forvo = ForvoAPI(forvo_api_key)
        self.anki = anki_connector

    def process_card_batch(self, cards: List[Dict], additional_info: str = "") -> tuple[List[Dict], str]:
        """Process a batch of cards using Claude"""

        # Prepare card data for Claude
        card_data = []
        for card in cards:
            note = card.get("note", {})
            fields = note.get("fields", {})

            card_info = {
                "note_id": note.get("noteId"),
                "model_name": note.get("modelName"),
                "fields": fields,
                "tags": note.get("tags", []),
            }
            card_data.append(card_info)

        if len(cards) == 0:
            print("No cards to process")
            return [], ""

        # Create prompt for Claude
        prompt = self._create_processing_prompt(card_data, additional_info)
        print(
            f"Prompt created, system: {len(prompt[0])} chars, user: {len(prompt[1])} chars for {len(cards)} cards"
        )

        try:
            print("Calling Claude API...")
            system_prompt, user_prompt = prompt
            response = self.client.messages.create(
                model=MODEL_NAME,
                max_tokens=4000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            # Store raw response for debugging
            raw_claude_response = response.content[0].text

            # Process cards and potentially add audio
            processed_cards = self._parse_claude_response(raw_claude_response)
            print(f"Parsed {len(processed_cards)} cards from Claude response")

            # Add Forvo audio where appropriate
            for card in processed_cards:
                self._add_forvo_audio(card)

            return processed_cards, raw_claude_response

        except Exception as e:
            print(f"Error processing batch with Claude: {e}")
            traceback.print_exc()
            return [], ""

    def _create_processing_prompt(self, card_data: List[Dict], additional_info: str = "") -> tuple:
        """Create the system and user prompts for Claude to process cards.
        Returns a tuple of (system_prompt, user_prompt)."""

        if os.path.exists("prompt.md"):
            system_prompt = open("prompt.md", "r", encoding="utf-8").read()
        else:
            system_prompt = open("anki_deck_fixer/prompt.md", "r", encoding="utf-8").read()

        user_prompt = f"""Process the following cards and return only the results strictly in the JSON format specified in your instructions, with no further comments.
Cards to process:
{json.dumps(card_data, indent=2, ensure_ascii=False)}
"""
        if additional_info:
            user_prompt += f"\nAdditional instructions from the user:\n{additional_info}\n"

        return (system_prompt, user_prompt)

    def _parse_claude_response(self, response_text: str) -> List[Dict]:
        """Parse Claude's JSON response and prepare updates"""
        try:
            # Extract JSON from response (Claude might wrap it in text)
            start_idx = response_text.find("{")
            end_idx = response_text.rfind("}") + 1

            if start_idx == -1 or end_idx == 0:
                print("No JSON found in Claude's response")
                return []

            json_str = response_text[start_idx:end_idx]
            parsed_response = json.loads(json_str)

            processed_cards = parsed_response.get("processed_cards", [])

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

        updated_fields = card.get("updated_fields", {})
        front_field = updated_fields.get("Front", "")

        # Extract the main word from the front field (remove articles, parentheses, etc.)
        word = self._extract_main_word(front_field)

        if word and not updated_fields.get("Audio"):
            print(f"  Downloading audio for '{word}'...")

            audio_data = self.forvo.download_pronunciation(word)
            if audio_data:
                # Store the audio file in Anki's media collection
                if self.anki.store_media_file(
                    audio_data["filename"], audio_data["data"]
                ):
                    # Create audio tag for Anki
                    audio_tag = f"[sound:{audio_data['filename']}]"
                    updated_fields["Audio"] = audio_tag
                    card["updated_fields"] = updated_fields

                    print(f"  ✓ Audio added: {audio_data['filename']}")
                else:
                    print(f"  ✗ Failed to store audio file for '{word}'")
            else:
                print(f"  - No audio found for '{word}'")

    def _extract_main_word(self, front_field: str) -> str:
        """Extract the main Swedish word from the front field"""
        # Remove HTML tags
        clean_text = re.sub(r"<[^>]+>", "", front_field)

        # Remove articles
        clean_text = re.sub(
            r"^(en|ett|den|det|att)\s+", "", clean_text, flags=re.IGNORECASE
        )

        # Remove parentheses and their contents
        clean_text = re.sub(r"\([^)]*\)", "", clean_text)

        # Take the first word
        words = clean_text.strip().split()
        return words[0] if words else ""


class AnkiDeckFixer:
    """Main class to orchestrate the deck fixing process"""

    def __init__(
        self,
        claude_api_key: str,
        forvo_api_key: Optional[str] = None,
        should_create_backup: bool = True,
    ):
        self.anki = AnkiConnector()
        self.processor = SwedishCardProcessor(claude_api_key, forvo_api_key, self.anki)
        self.backup_created = False
        self.should_create_backup = should_create_backup

    def create_backup(self, deck_name: str) -> Optional[str]:
        """Create backup of the deck if enabled"""
        if not self.should_create_backup:
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{deck_name}_backup_{timestamp}"
        backup_path = f"./{backup_name}.apkg"

        try:
            print(f"Creating backup of deck '{deck_name}' as '{backup_path}'...")
            start_time = time.time()
            self.anki.export_deck(deck_name, backup_path)
            print(
                f"✓ Backup created: {backup_path} in {time.time() - start_time:.2f} seconds"
            )
            self.backup_created = True
            return backup_path
        except Exception as e:
            print(f"✗ Failed to create backup: {e}")
            raise

    def process_deck(self, card_ids: List[int], batch_size: int):
        """Process the entire deck in batches"""

        # Process in batches
        processed_count = 0
        for i in range(0, len(card_ids), batch_size):
            batch_card_ids = card_ids[i : i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(card_ids) + batch_size - 1) // batch_size

            print(
                f"\n--- Processing batch {batch_num}/{total_batches} ({len(batch_card_ids)} cards) ---"
            )

            # Get card info
            try:
                cards_info = self.anki.get_card_info(batch_card_ids)

                # Get unique note IDs and their info
                note_ids = list(set([card["note"] for card in cards_info]))
                notes_info = self.anki.get_note_info(note_ids)

                # Combine card and note info
                enriched_cards = []
                for card in cards_info:
                    note_id = card["note"]
                    note_info = next(
                        (n for n in notes_info if n["noteId"] == note_id), {}
                    )
                    card["note"] = note_info
                    enriched_cards.append(card)

                # Process with Claude
                processed_cards, full_log = self.processor.process_card_batch(
                    enriched_cards
                )

                if not processed_cards:
                    print("No changes suggested by Claude for this batch")
                    continue

                # Review changes before applying
                print(f"\nClaude suggests {len(processed_cards)} changes:")
                for card in processed_cards:
                    # Get the original card info to show the front field
                    original_card = next(
                        (
                            c
                            for c in enriched_cards
                            if c["note"]["noteId"] == card["note_id"]
                        ),
                        None,
                    )
                    if original_card:
                        front_field = original_card["note"]["fields"].get(
                            "Front", "Unknown"
                        )
                        print(f"\n--- Card: {front_field} ---")

                        # Show field changes with diff formatting
                        updated_fields = card.get("updated_fields", {})
                        original_fields = original_card["note"]["fields"]

                        for field_name, new_value in updated_fields.items():
                            old_value = original_fields.get(field_name, {}).get(
                                "value", ""
                            )
                            if old_value != new_value:
                                DiffFormatter.print_field_changes(
                                    field_name, old_value, new_value
                                )

                # Ask for confirmation
                response = input(
                    f"\nApply these changes to batch {batch_num}? (y/n/s=skip/q=quit): "
                ).lower()

                if response == "q":
                    print("Stopping processing.")
                    break
                elif response == "s":
                    print("Skipping this batch.")
                    continue
                elif response != "y":
                    print("Skipping this batch.")
                    continue

                # Apply changes
                changes_applied = 0
                for card in processed_cards:
                    try:
                        print(f"\nApplying changes to note ID {card}...")

                        note_id = card["note_id"]
                        updated_fields = card.get("updated_fields", {})

                        if updated_fields:
                            for field_name, new_value in updated_fields.items():
                                new_value = new_value.replace("\n", "<br>")
                                updated_fields[field_name] = new_value

                            prev_tags = self.anki.get_note_tags(note_id)
                            tags = prev_tags + ["reviewed"]
                            self.anki.update_note(note_id, updated_fields, tags)
                            changes_applied += 1

                    except Exception as e:
                        print(f"✗ Failed to update note {card['note_id']}: {e}")

                print(f"✓ Applied {changes_applied} changes in batch {batch_num}")
                processed_count += changes_applied

            except Exception as e:
                print(f"✗ Error processing batch {batch_num}: {e}")
                continue

            # Small delay to be respectful to APIs
            time.sleep(1)

        print("=== Processing Complete ===")
        print(f"Total cards processed: {processed_count}")

    def process_cards_for_review(
        self,
        deck_name: str,
        batch_size: int = 10,
        start_from: int = 0,
        word_list: Optional[str] = None,
        flagged_only: bool = False,
    ) -> Dict[str, Any]:
        """Process cards and return results for web interface review"""

        # Initialize skipped cards tracking
        skipped_cards = []

        # Verify deck exists
        deck_names = self.anki.get_deck_names()

        if deck_name not in deck_names:
            raise Exception(
                f"Deck '{deck_name}' not found. Available decks: {', '.join(deck_names)}"
            )

        # Create backup if enabled
        self.create_backup(deck_name)

        # Build target card list
        card_ids = []
        enriched_cards = []  # Initialize for both word_list and regular paths
        if word_list:
            # Use provided word list to filter cards (like --word_list)
            words = [word.strip() for word in word_list.split(",") if word.strip()]
            print(f"Filtering cards to only include words: {', '.join(words)}")
            seen = set()
            updated_word_count = 0
            new_word_count = 0
            for word in words:
                search = f"\"front:re:^.*\\b{word}\\b.*$\""
                results = self.anki.get_cards_in_deck_with_search(deck_name, search)
                if results:
                    # If found more than 1, skip
                    if len(results) > 1:
                        skipped_cards.append({
                            "word": word,
                            "reason": "multiple_matches",
                            "details": f"Found {len(results)} cards matching '{word}'. Please be more specific."
                        })
                        print(f"Skipping word {word} which had {len(results)} match results")
                        continue
                    for cid in results:
                        if cid not in seen:
                            card_ids.append(cid)
                            seen.add(cid)
                            updated_word_count += 1
                else:
                    # Create placeholder card for missing word (will be created on apply)
                    word_capitalized = word[0].upper() + word[1:]
                    placeholder_card = {
                        "cardId": f"new_{word}",  # Special ID to indicate new card
                        "note": {
                            "noteId": f"new_{word}",
                            "fields": {
                                "Front": {"value": word_capitalized},
                                "Back": {"value": ""},
                                "Audio": {"value": ""},
                            },
                            "tags": []
                        },
                        "is_new_card": True
                    }
                    # Add to enriched cards list for Claude processing
                    enriched_cards.append(placeholder_card)
                    card_ids.append(f"new_{word}")
                    seen.add(f"new_{word}")
                    new_word_count += 1

            print(
                f"Found {updated_word_count} existing words, added {new_word_count} new words, total {len(card_ids)} cards to review"
            )
        else:
            search = ""
            if flagged_only:
                search = " flag:1"
            else:
                search = "-tag:reviewed is:new" # By default only process new, unreviewed cards
            card_ids = self.anki.get_cards_in_deck_with_search(deck_name, search)

            if len(card_ids) == 0:
                print("Found 0 cards to review")
                return {
                    "deck_name": deck_name,
                    "batch_size": batch_size,
                    "start_from": start_from,
                    "processed_count": 0,
                    "processed_cards": [],
                    "full_log": "",
                }

            # Sort cards to prioritize important ones
            print(f"Sorting {len(card_ids)} cards by priority...")
            card_ids = self._sort_cards_by_priority(card_ids)

        if start_from > 0:
            card_ids = card_ids[start_from:]
            print(f"Sliced to start from {start_from}, now have {len(card_ids)} cards")

        # Limit to batch_size for processing
        if len(card_ids) > batch_size:
            card_ids = card_ids[:batch_size]
            print(f"Batch size: {batch_size}, processing {len(card_ids)} cards")

        # If after filtering there are no cards, return empty
        if not card_ids:
            print("Found 0 cards to process after filtering")
            return {
                "deck_name": deck_name,
                "batch_size": batch_size,
                "start_from": start_from,
                "processed_count": 0,
                "processed_cards": [],
                "full_log": "",
            }

        # Get card info and handle placeholder cards
        real_card_ids = []
        
        # Separate real card IDs from placeholder IDs
        for card_id in card_ids:
            if not isinstance(card_id, str) or not card_id.startswith("new_"):
                real_card_ids.append(card_id)
        
        # Get info for real cards
        if real_card_ids:
            cards_info = self.anki.get_card_info(real_card_ids)
            
            # Get unique note IDs and their info
            note_ids = set()
            for i, card in enumerate(cards_info):
                if card.get("note") is not None:
                    note_ids.add(card["note"])
                else:
                    skipped_cards.append({
                        "card_id": card.get("cardId", f"unknown_{i}"),
                        "reason": "missing_note",
                        "details": f"Card doesn't contain note property, skipping: {card} ({i})"
                    })
                    print(f"Card doesn't contain note property, skipping: {card} ({i})")
            note_ids = list(note_ids)
            notes_info = self.anki.get_note_info(note_ids)
            
            # Combine card and note info
            for card in cards_info:
                if card.get("note") is not None:
                    note_id = card["note"]
                    note_info = next((n for n in notes_info if n["noteId"] == note_id), {})
                    card["note"] = note_info
                    enriched_cards.append(card)

        # Process with Claude
        print("Processing with Claude API...")
        processed_cards, full_log = self.processor.process_card_batch(enriched_cards)
        print(f"Claude processing complete, got {len(processed_cards)} processed cards")

        # Re-attach is_new_card flag for placeholder cards that Claude processed
        for processed_card in processed_cards:
            note_id = processed_card.get("note_id", "")
            if isinstance(note_id, str) and note_id.startswith("new_"):
                processed_card["is_new_card"] = True

        # Add original fields for comparison
        for processed_card in processed_cards:
            note_id = processed_card["note_id"]
            original_card = next(
                (c for c in enriched_cards if c["note"]["noteId"] == note_id), None
            )
            if original_card:
                processed_card["original_fields"] = original_card["note"]["fields"]

        # Sanitize processed cards for JSON serialization
        def sanitize_for_json(obj):
            """Recursively sanitize object for JSON serialization"""
            if isinstance(obj, dict):
                return {k: sanitize_for_json(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [sanitize_for_json(item) for item in obj]
            elif isinstance(obj, str):
                # Replace problematic characters that might break JSON
                return obj.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
            else:
                return obj

        sanitized_cards = sanitize_for_json(processed_cards)

        return {
            "deck_name": deck_name,
            "batch_size": batch_size,
            "start_from": start_from,
            "processed_count": len(processed_cards),
            "processed_cards": sanitized_cards,
            "full_log": full_log,
            "skipped_cards": skipped_cards,
        }

    def apply_selected_changes(self, changes_data: Dict[str, Any]) -> Dict[str, Any]:
        """Apply selected changes from the web interface"""

        selected_cards = changes_data.get("cards", [])
        deck_name = changes_data.get("deck_name")
        results = {"applied_count": 0, "failed_count": 0, "errors": []}

        for card in selected_cards:
            try:
                note_id = card["note_id"]
                updated_fields = card.get("updated_fields", {})

                # Check if this is a new card placeholder
                if card.get("is_new_card", False) and isinstance(note_id, str) and note_id.startswith("new_"):
                    # Create new card
                    if updated_fields:
                        for field_name, new_value in updated_fields.items():
                            new_value = new_value.replace("\n", "<br>")
                            updated_fields[field_name] = new_value

                        # Create the new note in Anki
                        new_note_id = self.anki.add_note(
                            deck_name,
                            "Basic (with audio)",
                            updated_fields,
                            ["reviewed"]
                        )
                        
                        if new_note_id:
                            results["applied_count"] += 1
                            print(f"✓ Created new card for word: {updated_fields.get('Front', 'unknown')}")
                        else:
                            raise Exception("Failed to create new note")
                else:
                    # Update existing card - always update to add reviewed tag
                    if updated_fields:
                        for field_name, new_value in updated_fields.items():
                            new_value = new_value.replace("\n", "<br>")
                            updated_fields[field_name] = new_value

                        # TODO: Add forvo audio & change note type when needed

                    tags = self.anki.get_note_tags(note_id) + ["reviewed"]
                    self.anki.update_note(note_id, updated_fields, tags)
                    results["applied_count"] += 1

            except Exception as e:
                results["failed_count"] += 1
                results["errors"].append(
                    f"Note {card.get('note_id', 'unknown')}: {str(e)}"
                )

        return results

    def _sort_cards_by_priority(self, card_ids: List[int]) -> List[int]:
        if not card_ids:
            return card_ids

        # Get card info including stats
        cards_info = self.anki.get_card_info(card_ids)

        # Order new cards by their new-position due (ascending)
        new_cards_sorted = sorted(cards_info, key=lambda c: int(c.get("due", 0)))

        sorted_card_ids = [c.get("cardId", 0) for c in new_cards_sorted]

        # Log selection results
        print("Selection complete (new cards only):")
        print(f"  - Total input cards: {len(cards_info)}")
        print(f"  - Selected new cards (reps=0): {len(sorted_card_ids)}")

        return sorted_card_ids


def main():
    """Main entry point"""

    # Parse command line arguments
    args = parse_arguments()

    if args.word_list and args.flagged_only:
        print(
            "Warning: --word_list and --flagged_only are mutually exclusive, --flagged_only will be ignored"
        )

    # Check for Claude API key
    claude_api_key = os.getenv("ANTHROPIC_API_KEY")
    if not claude_api_key:
        print("✗ Please set your ANTHROPIC_API_KEY environment variable")
        print("  Export it in your shell: export ANTHROPIC_API_KEY='your-key-here'")
        return

    # Check for optional Forvo API key
    forvo_api_key = os.getenv("FORVO_API_KEY")
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
        return

    # Handle web interface
    if args.web:
        try:
            server = start_web_server(fixer, args.port)
            print("Press Ctrl+C to stop the server")

            # Keep the server running
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n🛑 Shutting down server...")
                server.shutdown()
                print("✓ Server stopped")
        except Exception as e:
            print(f"✗ Error starting web server: {e}")
        return

    # Get available decks
    try:
        decks = fixer.anki.get_deck_names()

        # Get deck selection
        if args.deck:
            deck_name = args.deck
            if deck_name not in decks:
                print(f"✗ Deck '{deck_name}' not found")
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

        if start_from > 0:
            start_from -= 1  # Convert to 0-based index

        # Show configuration
        print("\nConfiguration:")
        print(f"  Deck: {deck_name}")
        print(f"  Batch size: {batch_size}")
        print(f"  Start from: card {args.start_from if args.start_from > 0 else 1}")
        print(f"  Backup: {'enabled' if should_create_backup else 'disabled'}")
        print(f"  Audio: {'enabled' if forvo_api_key else 'disabled'}")

        if not args.deck:
            confirm = input("\nProceed? (y/n): ").lower()
            if confirm != "y":
                print("Cancelled.")
                return

        print(f"\nStarting to process deck '{deck_name}'")
        print("Press Ctrl+C at any time to stop safely")

        # Verify deck exists
        deck_names = fixer.anki.get_deck_names()
        if deck_name not in deck_names:
            print(
                f"✗ Deck '{deck_name}' not found. Available decks: {', '.join(deck_names)}"
            )
            return

        # Create backup if enabled
        fixer.create_backup(deck_name)

        card_ids = []

        if args.parse_offline_updates:
            for update in offline_updates:
                fixer.anki.request("updateNote", **update)
            return

        if args.word_list:
            # Use provided word list to filter cards
            existing_words = [
                word.strip() for word in args.word_list.split(",") if word.strip()
            ]
            print(f"Filtering cards to only include words: {', '.join(existing_words)}")
            for word in existing_words:
                search = f"\"front:re:^.*\\b{word}\\b.*$\""
                results = fixer.anki.get_cards_in_deck_with_search(deck_name, search)
                if results:
                    print(f"Found {len(results)} cards for word '{word}'")
                    card_ids.extend(results)
            print(f"Found {len(card_ids)} matching cards in deck '{deck_name}'")
        else:
            # Get all cards in deck
            search = ""
            if args.flagged_only:
                search = " flag:1"
            else:
                search = "-tag:reviewed is:new" # By default only process new, unreviewed cards
            card_ids: List[int] = fixer.anki.get_cards_in_deck_with_search(
                deck_name, search
            )
            print(f"Found {len(card_ids)} cards in deck '{deck_name}'")

            # Sort cards to prioritize important ones
            print("Sorting cards by priority...")
            card_ids = fixer._sort_cards_by_priority(card_ids)

        if start_from > 0:
            card_ids = card_ids[start_from:]
            print(f"Starting from card {start_from + 1}")

        fixer.process_deck(card_ids, batch_size)

    except KeyboardInterrupt:
        print("\n\nProcessing interrupted by user.")
    except Exception as e:
        print(f"✗ Error: {e}")


if __name__ == "__main__":
    main()
