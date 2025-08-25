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
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import webbrowser
from urllib.parse import urlparse, parse_qs
import traceback

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
    
    def get_deck_names(self) -> List[str]:
        """Get all deck names"""
        return self.request("deckNames")
    
    def get_cards_in_deck(self, deck_name: str) -> List[int]:
        """Get all card IDs in a deck"""
        return self.request("findCards", query=f"deck:\"{deck_name}\"")
    
    def get_card_info(self, card_ids: List[int]) -> List[Dict]:
        """Get card information"""
        return self.request("cardsInfo", cards=card_ids)
    
    def update_note_fields(self, note_id: int, fields: Dict[str, str], model_name: Optional[str] = None):
        """Update note fields"""
        params = {
            "note": {
                "id": note_id,
                "fields": fields
            }
        }
        if model_name:
            params["note"]["modelName"] = model_name
            
        return self.request("updateNoteFields", **params)
    
    def create_deck(self, deck_name: str):
        """Create a new deck"""
        return self.request("createDeck", deck=deck_name)
    
    def export_deck(self, deck_name: str, path: str):
        """Export deck to file"""
        # Ensure the path is absolute and in current directory
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        return self.request("exportPackage", deck=deck_name, path=path, includeSched=False)
    
    def get_note_info(self, note_ids: List[int]) -> List[Dict]:
        """Get note information"""
        return self.request("notesInfo", notes=note_ids)
    
    def store_media_file(self, filename: str, data: bytes) -> bool:
        """Store media file in Anki's media collection"""
        try:
            import base64
            encoded_data = base64.b64encode(data).decode('utf-8')
            
            result = self.request("storeMediaFile", 
                                filename=filename, 
                                data=encoded_data)
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
            if path == '/' or path == '/index.html':
                self.serve_interface()
            elif path == '/api/decks':
                self.serve_decks()
            elif path == '/api/status':
                self.serve_status()
            else:
                self.send_error(404)
        except Exception as e:
            print(f"Error handling GET {path}: {e}")
            traceback.print_exc()
            self.send_error(500, str(e))
    
    def do_POST(self):
        """Handle POST requests"""
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(post_data) if post_data else {}
            
            if path == '/api/process':
                self.handle_process_request(data)
            elif path == '/api/apply':
                self.handle_apply_request(data)
            else:
                self.send_error(404)
        except Exception as e:
            print(f"Error handling POST {path}: {e}")
            traceback.print_exc()
            self.send_error(500, str(e))
    
    def serve_interface(self):
        """Serve the main HTML interface"""
        # Read the HTML content from the artifact or inline it
        html_content = self.get_interface_html()
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.send_header('Content-Length', str(len(html_content)))
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))
    
    def serve_decks(self):
        """Serve list of available decks"""
        try:
            if not self.fixer:
                raise Exception("Fixer not initialized")
            
            decks = self.fixer.anki.get_deck_names()
            response = {'decks': decks}
            self.send_json_response(response)
        except Exception as e:
            self.send_error(500, str(e))
            

    def serve_status(self):
        """Serve server status"""
        response = {
            'status': 'running',
            'claude_api': bool(os.getenv('ANTHROPIC_API_KEY')),
            'forvo_api': bool(os.getenv('FORVO_API_KEY')),
            'anki_connected': False
        }
        
        # Test Anki connection
        try:
            if self.fixer:
                self.fixer.anki.get_deck_names()
                response['anki_connected'] = True
        except:
            pass
        
        self.send_json_response(response)
    
    def handle_process_request(self, data):
        """Handle card processing request"""
        try:
            deck_name = data.get('deck_name')
            batch_size = data.get('batch_size', 10)
            start_from = data.get('start_from', 0)
            
            if not deck_name:
                raise Exception("deck_name is required")
            
            if not self.fixer:
                raise Exception("Fixer not initialized")
            
            print(f"Processing {batch_size} cards from deck '{deck_name}' starting at {start_from}")
            
            results = self.fixer.process_cards_for_review(deck_name, batch_size, start_from)
            self.send_json_response(results)
            
        except Exception as e:
            self.send_error(500, str(e))
    
    def handle_apply_request(self, data):
        """Handle apply changes request"""
        try:
            if not self.fixer:
                raise Exception("Fixer not initialized")
            
            results = self.fixer.apply_selected_changes(data)
            self.send_json_response(results)
            
        except Exception as e:
            self.send_error(500, str(e))
    
    def send_json_response(self, data):
        """Send JSON response"""
        response_data = json.dumps(data, ensure_ascii=False)
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', str(len(response_data)))
        self.end_headers()
        self.wfile.write(response_data.encode('utf-8'))
    
    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def log_message(self, format, *args):
        """Override to reduce log noise"""
        if not self.path.startswith('/api/'):
            return
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.command} {self.path} - {format % args}")
    
    def get_interface_html(self):
        """Get the HTML interface content"""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Anki Card Fixer - Web Interface</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; background: white; border-radius: 15px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); overflow: hidden; }
        .header { background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%); color: white; padding: 30px; text-align: center; }
        .header h1 { font-size: 2.5rem; margin-bottom: 10px; font-weight: 300; }
        .header p { opacity: 0.9; font-size: 1.1rem; }
        .controls { padding: 20px 30px; background: #f8f9fa; border-bottom: 1px solid #e9ecef; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; }
        .control-group { display: flex; align-items: center; gap: 15px; }
        .btn { padding: 12px 24px; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500; transition: all 0.3s ease; text-decoration: none; display: inline-flex; align-items: center; gap: 8px; }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .btn-primary { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
        .btn-primary:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4); }
        .btn-secondary { background: #6c757d; color: white; }
        .btn-success { background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; }
        .btn-danger { background: linear-gradient(135deg, #dc3545 0%, #fd7e14 100%); color: white; }
        .main-content { padding: 30px; }
        .deck-selector { background: #f8f9fa; border-radius: 12px; padding: 25px; margin-bottom: 30px; }
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
        .card { background: white; border: 1px solid #e9ecef; border-radius: 12px; margin-bottom: 20px; overflow: hidden; transition: all 0.3s ease; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
        .card:hover { box-shadow: 0 5px 20px rgba(0,0,0,0.1); }
        .card.selected { border-color: #667eea; box-shadow: 0 5px 20px rgba(102, 126, 234, 0.2); }
        .card-header { background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); padding: 20px; border-bottom: 1px solid #e9ecef; display: flex; align-items: center; justify-content: space-between; }
        .card-title { font-size: 1.2rem; font-weight: 600; color: #2c3e50; display: flex; align-items: center; gap: 15px; }
        .checkbox-wrapper { display: flex; align-items: center; gap: 10px; }
        .custom-checkbox { width: 20px; height: 20px; border: 2px solid #ddd; border-radius: 4px; cursor: pointer; transition: all 0.3s ease; display: flex; align-items: center; justify-content: center; }
        .custom-checkbox.checked { background: #667eea; border-color: #667eea; color: white; }
        .card-body { padding: 0; }
        .field-group { border-bottom: 1px solid #f1f3f4; padding: 20px; }
        .field-group:last-child { border-bottom: none; }
        .field-label { font-weight: 600; color: #495057; margin-bottom: 10px; display: block; }
        .field-comparison { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .field-section { background: #f8f9fa; border-radius: 8px; padding: 15px; }
        .field-section h4 { color: #6c757d; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; }
        .field-content { min-height: 60px; max-height: 200px; overflow-y: auto; font-family: 'Consolas', 'Monaco', monospace; font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
        .field-input { width: 100%; min-height: 100px; padding: 15px; border: 1px solid #ddd; border-radius: 8px; font-family: inherit; font-size: 14px; resize: vertical; transition: border-color 0.3s ease; }
        .field-input:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); }
        .changes-list { background: #e8f4f8; border-left: 4px solid #17a2b8; padding: 15px; margin-top: 15px; border-radius: 0 8px 8px 0; }
        .uncertain-changes { background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin-top: 10px; border-radius: 0 8px 8px 0; }
        .stats { display: flex; gap: 20px; align-items: center; font-weight: 500; color: #495057; }
        .stat-item { display: flex; align-items: center; gap: 8px; }
        .empty-state { text-align: center; padding: 60px 20px; color: #6c757d; }
        @media (max-width: 768px) { .field-comparison { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Anki Card Fixer</h1>
            <p>AI-powered Swedish flashcard improvement tool</p>
        </div>

        <div class="controls" id="mainControls">
            <div class="control-group">
                <div class="status-indicator" id="statusIndicator">Connecting...</div>
            </div>
            <div class="stats" id="statsDisplay" style="display: none;">
                <div class="stat-item"><span>📊</span><span>Total: <span id="totalCards">0</span></span></div>
                <div class="stat-item"><span>✅</span><span>Selected: <span id="selectedCards">0</span></span></div>
                <div class="stat-item"><span>⚠️</span><span>Uncertain: <span id="uncertainCards">0</span></span></div>
            </div>
            <div class="control-group" id="actionControls" style="display: none;">
                <button class="btn btn-secondary" onclick="selectAll()">Select All</button>
                <button class="btn btn-secondary" onclick="selectNone()">Select None</button>
                <button class="btn btn-success" onclick="applyChanges()" disabled id="applyBtn">Apply Changes</button>
            </div>
        </div>

        <div class="main-content">
            <div class="deck-selector" id="deckSelector">
                <h3 style="margin-bottom: 20px;">Process Cards</h3>
                <div style="display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 15px; margin-bottom: 20px;">
                    <div class="form-group">
                        <label for="deckSelect">Select Deck:</label>
                        <select id="deckSelect" class="form-control"></select>
                    </div>
                    <div class="form-group">
                        <label for="batchSize">Batch Size:</label>
                        <input type="number" id="batchSize" class="form-control" value="10" min="1" max="100">
                    </div>
                    <div class="form-group">
                        <label for="startFrom">Start From:</label>
                        <input type="number" id="startFrom" class="form-control" value="0" min="0">
                    </div>
                    <div class="form-group">
                        <label>&nbsp;</label>
                        <button class="btn btn-primary" onclick="processCards()" id="processBtn" style="width: 100%;">Fix Cards</button>
                    </div>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px;">
                    <div>
                        <label><input type="checkbox" id="createBackup" checked> Create backup before processing</label>
                    </div>
                </div>
            </div>

            <div class="processing" id="processing">
                <div class="processing-spinner"></div>
                <p id="processingText">Processing cards with Claude AI...</p>
            </div>

            <div id="cardContainer" style="display: none;">
                <!-- Cards will be generated here -->
            </div>

            <div class="empty-state" id="emptyState" style="display: none;">
                <div style="font-size: 4rem; margin-bottom: 20px; opacity: 0.5;">📝</div>
                <h3>No cards to review</h3>
                <p>Select a deck and click "Fix Cards" to get started</p>
            </div>
        </div>
    </div>

    <script>
        let cardData = [];
        let selectedCards = new Set();
        let currentDeckName = '';

        document.addEventListener('DOMContentLoaded', function() {
            checkServerStatus();
            loadDecks();
        });

        async function checkServerStatus() {
            try {
                const response = await fetch('/api/status');
                const status = await response.json();
                
                const indicator = document.getElementById('statusIndicator');
                if (status.anki_connected && status.claude_api) {
                    indicator.textContent = 'Connected';
                    indicator.className = 'status-indicator status-connected';
                } else {
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
                const data = await response.json();
                
                const deckSelect = document.getElementById('deckSelect');
                deckSelect.innerHTML = '<option value="">Select a deck...</option>';
                
                data.decks.forEach(deck => {
                    const option = document.createElement('option');
                    option.value = deck;
                    option.textContent = deck;
                    deckSelect.appendChild(option);
                });
            } catch (error) {
                console.error('Error loading decks:', error);
                alert('Error loading decks. Make sure Anki is running with AnkiConnect.');
            }
        }

        async function processCards() {
            const deckName = document.getElementById('deckSelect').value;
            const batchSize = parseInt(document.getElementById('batchSize').value);
            const startFrom = parseInt(document.getElementById('startFrom').value);
            
            if (!deckName) {
                alert('Please select a deck');
                return;
            }

            currentDeckName = deckName;
            showProcessing();

            try {
                const response = await fetch('/api/process', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        deck_name: deckName,
                        batch_size: batchSize,
                        start_from: startFrom
                    })
                });

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
                }

                const data = await response.json();
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
            
            if (!confirm(`Apply changes to ${selectedCards.size} card(s)?`)) {
                return;
            }

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
                
                let message = `Successfully applied changes to ${result.applied_count} card(s)`;
                if (result.failed_count > 0) {
                    message += `\\n${result.failed_count} card(s) failed to update`;
                    if (result.errors.length > 0) {
                        message += ':\\n' + result.errors.join('\\n');
                    }
                }
                
                alert(message);
                
                selectedCards.clear();
                updateStats();
                
            } catch (error) {
                console.error('Error applying changes:', error);
                alert('Error applying changes: ' + error.message);
                hideProcessing();
            }
        }

        function showProcessing(text = 'Processing cards with Claude AI...') {
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
            selectedCards.clear();
            renderCards();
            updateStats();
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

        function createCardElement(card, index) {
            const cardDiv = document.createElement('div');
            cardDiv.className = 'card';
            cardDiv.id = `card-${index}`;

            const hasUncertain = card.uncertain_changes && card.uncertain_changes.length > 0;
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
                        ${hasUncertain ? '<span style="color: #ffc107;">⚠️ Uncertain</span>' : ''}
                    </div>
                </div>
                <div class="card-body">
                    ${renderFields(card, index)}
                    ${renderChanges(card)}
                </div>
            `;

            return cardDiv;
        }

        function getCardTitle(card) {
            const fields = card.updated_fields || {};
            const front = fields.Front || fields.front || '';
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
                const oldValue = originalFields[fieldName] || '';
                
                fieldsHtml += `
                    <div class="field-group">
                        <label class="field-label">${fieldName}</label>
                        <div class="field-comparison">
                            <div class="field-section">
                                <h4>Original</h4>
                                <div class="field-content">${escapeHtml(oldValue) || '<em>Empty</em>'}</div>
                            </div>
                            <div class="field-section">
                                <h4>Updated</h4>
                                <textarea class="field-input" 
                                         onchange="updateField(${cardIndex}, '${fieldName}', this.value)"
                                         placeholder="Enter ${fieldName} content...">${escapeHtml(newValue)}</textarea>
                            </div>
                        </div>
                    </div>
                `;
            });
            
            return fieldsHtml;
        }

        function renderChanges(card) {
            let changesHtml = '';
            
            if (card.changes_made && card.changes_made.length > 0) {
                changesHtml += `
                    <div class="changes-list">
                        <h4>Changes Made:</h4>
                        <ul>
                            ${card.changes_made.map(change => `<li>• ${escapeHtml(change)}</li>`).join('')}
                        </ul>
                    </div>
                `;
            }
            
            if (card.uncertain_changes && card.uncertain_changes.length > 0) {
                changesHtml += `
                    <div class="uncertain-changes">
                        <h4>Uncertain Changes (Review Carefully):</h4>
                        <ul>
                            ${card.uncertain_changes.map(change => `<li>⚠️ ${escapeHtml(change)}</li>`).join('')}
                        </ul>
                    </div>
                `;
            }
            
            return changesHtml;
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

        function updateStats() {
            document.getElementById('totalCards').textContent = cardData.length;
            document.getElementById('selectedCards').textContent = selectedCards.size;
            
            const uncertainCount = cardData.filter(card => 
                card.uncertain_changes && card.uncertain_changes.length > 0
            ).length;
            document.getElementById('uncertainCards').textContent = uncertainCount;
            
            const applyBtn = document.getElementById('applyBtn');
            applyBtn.disabled = selectedCards.size === 0;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
    </script>
</body>
</html>'''

def start_web_server(fixer, port: int = 8080):
    """Start the web server"""
    WebServer.fixer = fixer
    
    server = HTTPServer(('localhost', port), WebServer)
    
    print(f"🚀 Starting web server on http://localhost:{port}")
    print("🌐 Opening browser...")
    
    # Start server in a separate thread
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    # Open browser
    webbrowser.open(f'http://localhost:{port}')
    
    return server

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
        """
    )
    
    parser.add_argument('--deck', type=str, help='Deck name to process')
    parser.add_argument('--batch-size', type=int, default=10, help='Number of cards to process in each batch (default: 10)')
    parser.add_argument('--start-from', type=int, default=0, help='Card number to start from (1-based, default: 0)')
    parser.add_argument('--no-backup', action='store_true', help='Skip creating backup (not recommended)')
    parser.add_argument('--list-decks', action='store_true', help='List available decks and exit')
    parser.add_argument('--web', action='store_true', help='Start web interface')
    parser.add_argument('--port', type=int, default=8080, help='Port for web interface (default: 8080)')
    
    return parser.parse_args()

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
            note = card.get('note', {})
            fields = note.get('fields', {})
            
            card_info = {
                'note_id': note.get('noteId'),
                'model_name': note.get('modelName'),
                'fields': fields,
                'tags': note.get('tags', [])
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
            
            # Process cards and potentially add audio
            processed_cards = self._parse_claude_response(response.content[0].text, cards)
            
            # Add Forvo audio where appropriate
            for card in processed_cards:
                self._add_forvo_audio(card)
            
            return processed_cards
            
        except Exception as e:
            print(f"Error processing batch with Claude: {e}")
            return []
    
    def _create_processing_prompt(self, card_data: List[Dict]) -> str:
        """Create the prompt for Claude to process cards"""
        
        rules = """
## Rules

You are a language expert tasked with helping me build a Swedish flash card deck that I study to memorize words and phrases. Your current task is to go through my deck and fix it up according to the following rules:

1. A card ending in "autogenerated" should be entirely re-written. Find a definition and related words from wiktionary, synonyms from synonyms.se.
2. An example sentence in quotations prefixed with "t.ex." should be simply be in parenthesis, without the prefix.
3. A list of different defintions separated by "or, " should be converted into a numbered list with each item on a new line.
4. When a word has several definitions indicate the total count in the front field, for example: "En skorpa (2)".
5. Synonyms (in parenthesis coming after "syn: ") and additional information (such as "se även: " or related words) should use a darker grey text colour) and appear on a new line after the relevant definition.
6. Less common usages should be indicated as such, coming last in the list of definitions after a "mindre vanliga: " line.
7. Any sound tag beginning with "sound:hypertts" should be deleted. Then forvo should be checked to see if a relevant file exists there to replace it. No audio is better than this existing file.
9. Fix the spelling for any misspelled words.
10. Example sentences should be wrapped in quotations, and normally each be on their own line. The word in question should be in italics. The sentence (or partial sentence) should be as long as it needs to to show the word's usage but not longer.
11. If a word is reflexive, indicate that with "(refl)" at the start of the line, after the number.
12. Very uncommon words that are unlikely to be seen or heard today can be marked with flag 7 ("sällsynt").
13. Audio tags shouldn't be in the "front" field but rather in a separate field called "Audio". If the card type is "Basic" then it should be changed to "Basic (with audio)" so it has this additional field. 

For each card you review go through each rule and apply a fix when relevant.

## General guidelines
Always include the relevant preposition as this information is crucial to learn alongside the word, plus the word's article (en eller ett).

Write a brief but comprehensive log for all the changes you make as you go so I can later review it. At the end of the log write down any changes you were uncertain about that I'm more likely to want to know about.

Try to keep the cards short and concise yet with enough info to capture the precise meaning of a word or phrase. A single word in English most often doesn't suffice to comprehensively define a Swedish word in all its usages, conjugations, etc. I want to be able to quickly review cards and only rely on the additional information when a card isn't sticking. The most essential information should come first and be most prominent.
"""
        
        prompt = f"""{rules}

Please process the following cards and return the results in JSON format. For each card, indicate what changes were made and provide the updated fields.

Cards to process:
{json.dumps(card_data, indent=2, ensure_ascii=False)}

Return format:
{{
  "processed_cards": [
    {{
      "note_id": 123,
      "changes_made": ["description of change 1", "description of change 2"],
      "updated_fields": {{"Front": "new front", "Back": "new back", "audio": "new audio"}},
      "model_change": "basic (with audio)",  // only if model needs to change
      "uncertain_changes": ["things you're uncertain about"],
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
                note_id = card.get('note_id')
                changes = card.get('changes_made', [])
                uncertain = card.get('uncertain_changes', [])
                
                self.changes_log.append({
                    'note_id': note_id,
                    'timestamp': datetime.now().isoformat(),
                    'changes': changes,
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
                    
                    changes = card.get('changes_made', [])
                    changes.append(f"Added Forvo audio for '{word}' ({audio_data['votes']} votes)")
                    card['changes_made'] = changes
                    
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
    
    def __init__(self, claude_api_key: str, forvo_api_key: Optional[str] = None, create_backup: bool = True):
        self.anki = AnkiConnector()
        self.processor = SwedishCardProcessor(claude_api_key, forvo_api_key, self.anki)
        self.backup_created = False
        self.create_backup = create_backup
        
    def create_backup(self, deck_name: str) -> Optional[str]:
        """Create backup of the deck if enabled"""
        if not self.create_backup:
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
    
    def process_deck(self, deck_name: str, batch_size: int = 10, start_from: int = 0):
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
        
        if start_from > 0:
            card_ids = card_ids[start_from:]
            print(f"Starting from card {start_from + 1}")
        
        # Process in batches
        processed_count = 0
        for i in range(0, len(card_ids), batch_size):
            batch_card_ids = card_ids[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(card_ids) + batch_size - 1) // batch_size
            
            print(f"\n--- Processing batch {batch_num}/{total_batches} ({len(batch_card_ids)} cards) ---")
            
            # Get card info
            try:
                cards_info = self.anki.get_card_info(batch_card_ids)
                
                # Get unique note IDs and their info
                note_ids = list(set([card['note'] for card in cards_info]))
                notes_info = self.anki.get_note_info(note_ids)
                
                # Combine card and note info
                enriched_cards = []
                for card in cards_info:
                    note_id = card['note']
                    note_info = next((n for n in notes_info if n['noteId'] == note_id), {})
                    card['note'] = note_info
                    enriched_cards.append(card)
                
                # Process with Claude
                processed_cards = self.processor.process_card_batch(enriched_cards)
                
                if not processed_cards:
                    print("No changes suggested by Claude for this batch")
                    continue
                
                # Review changes before applying
                print(f"\nClaude suggests {len(processed_cards)} changes:")
                for card in processed_cards:
                    # Get the original card info to show the front field
                    original_card = next((c for c in enriched_cards if c['note']['noteId'] == card['note_id']), None)
                    if original_card:
                        front_field = original_card['note']['fields'].get('Front', 'Unknown')
                        print(f"\n--- Card: {front_field} ---")
                        
                        # Show field changes with diff formatting
                        updated_fields = card.get('updated_fields', {})
                        original_fields = original_card['note']['fields']
                        
                        for field_name, new_value in updated_fields.items():
                            old_value = original_fields.get(field_name, '')
                            if old_value != new_value:
                                DiffFormatter.print_field_changes(field_name, old_value, new_value)
                        
                        # Show general changes
                        changes = card.get('changes_made', [])
                        if changes:
                            print(f"  Changes: {', '.join(changes)}")
                        
                        if card.get('uncertain_changes'):
                            print(f"  \033[93mUncertain: {', '.join(card['uncertain_changes'])}\033[0m")
                
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
                    try:
                        note_id = card['note_id']
                        updated_fields = card.get('updated_fields', {})
                        model_change = card.get('model_change')
                        
                        if updated_fields:
                            self.anki.update_note_fields(note_id, updated_fields, model_change)
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
    
    def process_cards_for_review(self, deck_name: str, batch_size: int = 10, start_from: int = 0) -> Dict[str, Any]:
        """Process cards and return results for web interface review"""
        
        # Verify deck exists
        deck_names = self.anki.get_deck_names()
        if deck_name not in deck_names:
            raise Exception(f"Deck '{deck_name}' not found. Available decks: {', '.join(deck_names)}")
        
        # Create backup if enabled
        backup_path = self.create_backup(deck_name)
        
        # Get all cards in deck
        card_ids = self.anki.get_cards_in_deck(deck_name)
        total_cards = len(card_ids)
        
        if start_from > 0:
            card_ids = card_ids[start_from:]
        
        # Limit to batch_size for processing
        if len(card_ids) > batch_size:
            card_ids = card_ids[:batch_size]
        
        # Get card info
        cards_info = self.anki.get_card_info(card_ids)
        
        # Get unique note IDs and their info
        note_ids = list(set([card['note'] for card in cards_info]))
        notes_info = self.anki.get_note_info(note_ids)
        
        # Combine card and note info
        enriched_cards = []
        for card in cards_info:
            note_id = card['note']
            note_info = next((n for n in notes_info if n['noteId'] == note_id), {})
            card['note'] = note_info
            enriched_cards.append(card)
        
        # Process with Claude
        processed_cards = self.processor.process_card_batch(enriched_cards)
        
        # Add original fields for comparison
        for processed_card in processed_cards:
            note_id = processed_card['note_id']
            original_card = next((c for c in enriched_cards if c['note']['noteId'] == note_id), None)
            if original_card:
                processed_card['original_fields'] = original_card['note']['fields']
        
        return {
            'deck_name': deck_name,
            'total_cards_in_deck': total_cards,
            'batch_size': batch_size,
            'start_from': start_from,
            'processed_count': len(processed_cards),
            'backup_path': backup_path,
            'timestamp': datetime.now().isoformat(),
            'processed_cards': processed_cards
        }
    
    def apply_selected_changes(self, changes_data: Dict[str, Any]) -> Dict[str, Any]:
        """Apply selected changes from the web interface"""
        
        selected_cards = changes_data.get('cards', [])
        results = {
            'applied_count': 0,
            'failed_count': 0,
            'errors': []
        }
        
        for card in selected_cards:
            try:
                note_id = card['note_id']
                updated_fields = card.get('updated_fields', {})
                model_change = card.get('model_change')
                
                if updated_fields:
                    self.anki.update_note_fields(note_id, updated_fields, model_change)
                    results['applied_count'] += 1
                    
                    # Log the change
                    self.processor.changes_log.append({
                        'note_id': note_id,
                        'timestamp': datetime.now().isoformat(),
                        'changes': card.get('changes_made', []),
                        'applied_via': 'web_interface'
                    })
                    
            except Exception as e:
                results['failed_count'] += 1
                results['errors'].append(f"Note {card.get('note_id', 'unknown')}: {str(e)}")
        
        # Save change log
        if self.processor.changes_log:
            log_file = f"changes_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(log_file, 'w', encoding='utf-8') as f:
                json.dump(self.processor.changes_log, f, indent=2, ensure_ascii=False)
            results['log_file'] = log_file
        
        return results

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
        create_backup = not args.no_backup
        fixer = AnkiDeckFixer(claude_api_key, forvo_api_key, create_backup)
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
        print("Available decks:")
        for i, deck in enumerate(decks, 1):
            print(f"  {i}. {deck}")
        
        # Get deck selection
        if args.deck:
            deck_name = args.deck
            if deck_name not in decks:
                print(f"✗ Deck '{deck_name}' not found")
                return
        else:
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
        print(f"\nConfiguration:")
        print(f"  Deck: {deck_name}")
        print(f"  Batch size: {batch_size}")
        print(f"  Start from: card {args.start_from if args.start_from > 0 else 1}")
        print(f"  Backup: {'enabled' if create_backup else 'disabled'}")
        print(f"  Audio: {'enabled' if forvo_api_key else 'disabled'}")
        
        if not args.deck:
            confirm = input("\nProceed? (y/n): ").lower()
            if confirm != 'y':
                print("Cancelled.")
                return
        
        print(f"\nStarting to process deck '{deck_name}'")
        print("Press Ctrl+C at any time to stop safely")
        
        # Process the deck
        fixer.process_deck(deck_name, batch_size, start_from)
        
    except KeyboardInterrupt:
        print("\n\nProcessing interrupted by user.")
        if fixer.backup_created:
            print("Your original deck is safely backed up.")
        elif create_backup:
            print("No backup was created as processing was interrupted early.")
    except Exception as e:
        print(f"✗ Error: {e}")

if __name__ == "__main__":
    main()
