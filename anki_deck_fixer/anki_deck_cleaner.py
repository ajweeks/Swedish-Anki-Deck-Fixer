#!/usr/bin/env python3
"""
Anki Swedish Deck Cleaner - Version 2

This script cleans up an Anki deck according to specific formatting rules:
1. Text color: Only main definitions should have white text, examples and synonyms should be gray
2. Indicate number of definitions on front field
3. Replace HTML entities with actual characters
4. Remove "t.ex." before example sentences
5. Remove parentheses around example sentences

Prerequisites:
1. Install AnkiConnect add-on in Anki (code: 2055492159)
2. Install required packages: pip install requests
3. Have Anki running with AnkiConnect enabled
"""

import json
import requests
import re
import html
from typing import List, Dict, Tuple
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser
from urllib.parse import urlparse
import traceback


class AnkiConnector:
    """Handles communication with Anki through AnkiConnect"""

    def __init__(self, url="http://localhost:8765"):
        self.url = url

    def request(self, action: str, **params):
        """Send request to AnkiConnect"""
        payload = {"action": action, "version": 6, "params": params}

        try:
            response: requests.Response = requests.post(self.url, json=payload)
            response.raise_for_status()
            result = response.json()

            if result.get("error"):
                raise Exception(f"AnkiConnect error: {result['error']}")

            return result.get("result")
        except requests.exceptions.ConnectionError:
            raise Exception(
                "Cannot connect to Anki. Make sure Anki is running with AnkiConnect add-on installed."
            )

    def get_deck_names(self) -> List[str]:
        """Get all deck names"""
        return self.request("deckNames")

    def get_cards_in_deck(self, deck_name: str) -> List[int]:
        """Get all card IDs in a deck"""
        return self.request("findCards", query=f'deck:"{deck_name}"')

    def find_cards(self, query: str) -> List[int]:
        return self.request("findCards", query=query)

    def get_card_info(self, card_ids: List[int]) -> List[Dict]:
        """Get card information"""
        return self.request("cardsInfo", cards=card_ids)

    def get_note_info(self, note_ids: List[int]) -> List[Dict]:
        """Get note information"""
        return self.request("notesInfo", notes=note_ids)

    def update_note_fields(self, note_id: int, fields: Dict[str, str]) -> Dict:
        """Update note fields"""
        params = {"note": {"id": note_id, "fields": fields}}
        return self.request("updateNoteFields", **params)

    def multi(self, actions: List[Dict]) -> List:
        return self.request("multi", actions=actions)

    def get_model_names(self) -> List[str]:
        """Get all model names"""
        return self.request("modelNames")

    def get_model_field_names(self, model_name: str) -> List[str]:
        """Get field names for a model"""
        return self.request("modelFieldNames", modelName=model_name)


class CardCleaner:
    """Handles cleaning of individual cards according to the specified rules"""

    def __init__(self):
        # Pattern to match numbered definitions (1., 2., 3., etc.)
        self.numbered_def_pattern = re.compile(r'^(\d+)\.\s*(.*?)(?=<br><br>|$)', re.MULTILINE | re.DOTALL)
        # Pattern to match "Or, " separators
        self.or_separator_pattern = re.compile(r'<br>\s*Or,\s*', re.IGNORECASE)
        # Pattern to detect example sentences (starts with quote)
        self.example_sentence_pattern = re.compile(r'^"')
        # Pattern to match parentheses around example sentences
        self.parentheses_example_pattern = re.compile(r'^\(([^)]+)\)$')
        # Pattern to match RGB colors
        self.rgb_pattern = re.compile(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)')

        self._italic_terms = []
        self._italic_article = None

    def _set_italic_terms_from_front(self, front: str) -> None:
        text = '' if front is None else str(front)
        text = re.sub(r'\s*\[sound:[^\]]+\]\s*', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*\(\d+\)\s*$', '', text).strip()

        m = re.match(r'^(en|ett|att)\s+', text, flags=re.IGNORECASE)
        self._italic_article = (m.group(1).lower() if m else None)
        text = re.sub(r'^(?:en|ett|att)\s+', '', text, flags=re.IGNORECASE).strip()
        if not text:
            self._italic_terms = []
            return

        terms = []
        base_allow_suffix = (' ' not in text and len(text) >= 4)
        terms.append((text, base_allow_suffix))

        if ' ' not in text and len(text) >= 5 and text.lower().endswith('a'):
            stem = text[:-1]
            if stem:
                terms.append((stem, True))

        terms.sort(key=lambda t: len(t[0]), reverse=True)
        self._italic_terms = terms

    def _italicize_current_terms(self, html_text: str) -> str:
        if not self._italic_terms:
            return html_text

        tag_re = re.compile(r'(<[^>]+>)')
        parts = tag_re.split(html_text)
        out = []
        in_i = False

        for part in parts:
            if part is None or part == '':
                continue

            if tag_re.fullmatch(part):
                lower = part.lower()
                if lower.startswith('<i'):
                    in_i = True
                elif lower.startswith('</i'):
                    in_i = False
                out.append(part)
                continue

            if in_i:
                out.append(part)
                continue

            text_part = part

            italic_tag_re = re.compile(r'(<\/?i\b[^>]*>)', re.IGNORECASE)
            pa_paren_re = re.compile(r'(\(\s*på\b[^)]*\))', re.IGNORECASE)

            only_within_quotes = (self._italic_article == 'att')

            def apply_inside_quotes(source: str, fn) -> str:
                out_chars = []
                buf = []
                quote = None

                for ch in source:
                    if ch in ('"', "'"):
                        if quote is None:
                            out_chars.append(''.join(buf))
                            buf = []
                            quote = ch
                            out_chars.append(ch)
                        elif ch == quote:
                            out_chars.append(fn(''.join(buf)))
                            buf = []
                            quote = None
                            out_chars.append(ch)
                        else:
                            buf.append(ch)
                    else:
                        buf.append(ch)

                if quote is None:
                    out_chars.append(''.join(buf))
                else:
                    out_chars.append(fn(''.join(buf)))

                return ''.join(out_chars)

            def apply_outside_i(source: str, pattern: str) -> str:
                sub_parts = italic_tag_re.split(source)
                sub_out = []
                in_i_local = False
                for sub in sub_parts:
                    if not sub:
                        continue
                    if italic_tag_re.fullmatch(sub):
                        if sub.lower().startswith('<i'):
                            in_i_local = True
                        elif sub.lower().startswith('</i'):
                            in_i_local = False
                        sub_out.append(sub)
                        continue

                    if in_i_local:
                        sub_out.append(sub)
                    else:
                        def do_sub(s: str) -> str:
                            chunks = pa_paren_re.split(s)
                            if len(chunks) == 1:
                                return re.sub(
                                    pattern,
                                    lambda m: f'<i>{m.group(0)}</i>',
                                    s,
                                    flags=re.IGNORECASE,
                                )

                            out_chunks: List[str] = []
                            for chunk in chunks:
                                if not chunk:
                                    continue
                                if pa_paren_re.fullmatch(chunk):
                                    out_chunks.append(chunk)
                                else:
                                    out_chunks.append(
                                        re.sub(
                                            pattern,
                                            lambda m: f'<i>{m.group(0)}</i>',
                                            chunk,
                                            flags=re.IGNORECASE,
                                        )
                                    )
                            return ''.join(out_chunks)

                        if only_within_quotes:
                            sub_out.append(apply_inside_quotes(sub, do_sub))
                        else:
                            sub_out.append(do_sub(sub))

                return ''.join(sub_out)

            for term, allow_suffix in self._italic_terms:
                if not term:
                    continue

                if ' ' in term:
                    pattern = r'(?<!\w)' + re.escape(term) + r'(?!\w)'
                else:
                    if allow_suffix:
                        base = r'\b' + re.escape(term) + r'\w{0,3}\b'
                    else:
                        base = r'\b' + re.escape(term) + r'\b'

                    if self._italic_article == 'att':
                        pattern = r'(?<!\ben\s)(?<!\bett\s)' + base
                    elif self._italic_article in ('en', 'ett'):
                        pattern = r'(?<!\batt\s)' + base
                    else:
                        pattern = base

                text_part = apply_outside_i(text_part, pattern)

            out.append(text_part)

        return ''.join(out)

    def _gray_span_open_tag(self, span_html: str) -> str | None:
        if not span_html:
            return None

        gt = span_html.find('>')
        if gt <= 0:
            return None

        open_tag = span_html[:gt + 1]
        if not open_tag.lower().startswith('<span'):
            return None

        if re.search(r'color\s*:\s*#c2c2c2\b', open_tag, flags=re.IGNORECASE):
            return open_tag

        if re.search(
            r'color\s*:\s*rgb\(\s*194\s*,\s*194\s*,\s*194\s*\)\s*;?',
            open_tag,
            flags=re.IGNORECASE,
        ):
            return open_tag

        return None

    def _wrap_gray_span(self, inner: str) -> str:
        return f'<span style="color: rgb(194, 194, 194)">{inner}</span>'

    def _italicize_repeated_quoted_word(self, text: str) -> str:
        quoted = re.findall(r'"([^"]+)"', text)
        if len(quoted) < 2:
            return text

        word_pattern = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", re.UNICODE)
        per_quote_words = []
        for q in quoted:
            per_quote_words.append({w.lower() for w in word_pattern.findall(q)})

        counts = {}
        for words in per_quote_words:
            for w in words:
                counts[w] = counts.get(w, 0) + 1

        candidates = [w for w, c in counts.items() if c >= 2 and len(w) >= 4]
        if not candidates:
            return text

        max_len = max(len(w) for w in candidates)
        candidates = [w for w in candidates if len(w) == max_len]

        def wrap_outside_i(source: str, word: str) -> str:
            tag_re = re.compile(r'(<\/?i\b[^>]*>)', re.IGNORECASE)
            parts = tag_re.split(source)
            out = []
            in_i = False
            word_re = re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
            for part in parts:
                if not part:
                    continue
                if tag_re.fullmatch(part):
                    if part.lower().startswith('<i'):
                        in_i = True
                    elif part.lower().startswith('</i'):
                        in_i = False
                    out.append(part)
                    continue

                if in_i:
                    out.append(part)
                else:
                    out.append(word_re.sub(lambda m: f'<i>{m.group(0)}</i>', part))

            return ''.join(out)

        for w in candidates:
            text = wrap_outside_i(text, w)

        return text

    def clean_card(self, front: str, back: str) -> Tuple[str, str, bool]:
        """Clean a card's front and back fields
        
        Returns:
            Tuple of (new_front, new_back, changed) where changed indicates if any modifications were made
        """
        original_front = front
        original_back = back
        changed = False

        # Step 1: Decode HTML entities
        back = html.unescape(back)
        front = html.unescape(front)

        back = back.replace('\u00A0', ' ')
        front = front.replace('\u00A0', ' ')

        back = back.replace('\u00AD', ' ')
        front = front.replace('\u00AD', ' ')

        self._set_italic_terms_from_front(front)

        try:
            # Step 2: Extract and count definitions
            definitions = self._extract_definitions(back)
            def_count = len(definitions)
            
            # Step 3: Clean each definition
            cleaned_definitions = []
            for i, definition in enumerate(definitions, 1):
                cleaned_def = self._clean_definition(definition, is_main=(i == 1))
                cleaned_definitions.append(cleaned_def)
            
            # Step 4: Reconstruct back field
            if def_count > 1:
                # Numbered format for multiple definitions
                back = '<br><br>'.join([f"{i}. {def_text}" for i, def_text in enumerate(cleaned_definitions, 1)])
            else:
                # Single definition without number
                back = cleaned_definitions[0] if cleaned_definitions else back
            
            # Step 5: Update front field with definition count if > 1
            if def_count > 1:
                # Remove existing count if present
                front = re.sub(r'\s*\(\d+\)$', '', front)
                front = f"{front} ({def_count})"
            
            # Check if anything changed
            changed = (front != original_front) or (back != original_back)
            
            return front, back, changed

        finally:
            self._italic_terms = []
            self._italic_article = None

    def _extract_definitions(self, back: str) -> List[str]:
        """Extract individual definitions from the back field"""
        definitions = []
        
        # First try to split by numbered definitions
        # Look for pattern like "1. ...<br><br>2. ..."
        parts = re.split(r'<br><br>(?=\d+\.\s)', back)
        
        if len(parts) > 1:
            # We have numbered definitions
            for part in parts:
                # Remove the number prefix if present
                part = re.sub(r'^\d+\.\s*', '', part.strip())
                if part:
                    definitions.append(part)
        else:
            # Try to split by "Or, " separators
            parts = self.or_separator_pattern.split(back)
            if len(parts) > 1:
                definitions = [part.strip() for part in parts if part.strip()]
            else:
                # Single definition - keep as is
                definitions = [back.strip()]
        
        return definitions

    def _clean_definition(self, definition: str, is_main: bool) -> str:
        """Clean a single definition"""
        definition = self._normalize_gray_span_styles(definition)

        # First, handle t.ex. inside spans - remove t.ex. but keep the span and the quote
        definition = re.sub(
            r'(<span\b[^>]*>)\s*t\.ex\.\s*("([^"]*)")\s*</span>',
            r'\1\2</span>',
            definition,
            flags=re.IGNORECASE,
        )
        definition = self._normalize_gray_span_styles(definition)

        span_re = re.compile(r'(<span\b[^>]*>.*?</span>)', re.IGNORECASE | re.DOTALL)
        tokens = span_re.split(definition)

        out: List[str] = []
        for token in tokens:
            if not token:
                continue

            if span_re.fullmatch(token):
                # Preserve the entire span (spans can legitimately include <br> and <br><br>)
                span_html = self._normalize_gray_span_styles(token)

                open_tag = self._gray_span_open_tag(span_html)
                if open_tag and span_html.endswith('</span>'):
                    inner = span_html[len(open_tag):-len('</span>')]
                    inner = self._normalize_quoted_example_lines(inner)
                    inner = self._italicize_current_terms(inner)
                    span_html = f'{open_tag}{inner}</span>'

                prefix = ''.join(out)
                prefix_text = re.sub(r'(?:<br>)+', '', prefix).strip()
                allow_split = bool(prefix_text)
                span_html = self._maybe_split_gray_span_on_double_break(span_html, allow_split=allow_split)
                out.append(span_html)
            else:
                out.append(self._process_content_outside_spans(token, is_main))

        return ''.join(out)
    
    def _process_content_outside_spans(self, content: str, is_main: bool) -> str:
        """Process content that's not inside spans"""
        # Split by <br> to process lines
        lines = content.split('<br>')
        processed_lines = []

        idx = 0
        while idx < len(lines):
            line = lines[idx].strip()
            if not line:
                processed_lines.append(line)
                idx += 1
                continue

            m = re.match(r'^(.*)\(\s*(["\	\'].*)$', line)
            if m:
                def_part = m.group(1).rstrip()
                example_start = m.group(2).strip()
                if def_part and example_start.startswith(('"', "'")):
                    example_lines = [example_start]
                    next_idx = idx + 1
                    while next_idx < len(lines):
                        nxt = lines[next_idx].strip()
                        if not nxt:
                            break
                        if nxt.startswith(('"', "'")):
                            example_lines.append(nxt)
                            next_idx += 1
                            if nxt.rstrip().endswith(')'):
                                break
                            continue
                        break

                    examples = '<br>'.join(example_lines).strip()
                    if examples.startswith('('):
                        examples = examples[1:].lstrip()
                    if examples.rstrip().endswith(')'):
                        examples = examples.rstrip()[:-1].rstrip()
                    examples = self._normalize_quoted_example_lines(examples)

                    processed_lines.append(self._apply_color_styling(def_part, is_gray=False))
                    processed_lines.append(self._apply_color_styling(examples, is_gray=True))
                    idx = next_idx
                    continue

            # Handle t.ex. patterns
            if '(t.ex. "' in line.lower():
                parts = re.split(r'\(t\.ex\.\s*"([^"]*)"\)', line, flags=re.IGNORECASE)
                if len(parts) > 1:
                    def_part = parts[0].strip()
                    if def_part:
                        processed_lines.append(self._apply_color_styling(def_part, is_gray=False))

                    example_inner = f'"{parts[1]}"'

                    next_idx = idx + 1
                    next_line = lines[next_idx].strip() if next_idx < len(lines) else ''
                    if next_line and self._is_synonym_or_extra(next_line):
                        combined = f'{example_inner}<br>{next_line}'
                        processed_lines.append(self._apply_color_styling(combined, is_gray=True))
                        idx += 2
                        continue

                    processed_lines.append(self._apply_color_styling(example_inner, is_gray=True))

                    if len(parts) > 2:
                        remaining = parts[2].strip()
                        if remaining:
                            processed_lines.append(self._apply_color_styling(remaining, is_gray=True))
                else:
                    m = re.match(r'^\s*(.*?)\(\s*t\.ex\.\s*(.*?)\)\s*$', line, flags=re.IGNORECASE)
                    if m:
                        def_part = m.group(1).strip()
                        example_part = m.group(2).strip()

                        if def_part:
                            processed_lines.append(self._apply_color_styling(def_part, is_gray=False))

                        next_idx = idx + 1
                        next_line = lines[next_idx].strip() if next_idx < len(lines) else ''
                        if next_line and self._is_synonym_or_extra(next_line):
                            combined = f'{example_part}<br>{next_line}'
                            processed_lines.append(self._apply_color_styling(combined, is_gray=True))
                            idx += 2
                            continue

                        processed_lines.append(self._apply_color_styling(example_part, is_gray=True))
                    else:
                        line = re.sub(r'\(t\.ex\.\s*"', '"', line, flags=re.IGNORECASE)
                        line = re.sub(r'\)$', '', line)
                        processed_lines.append(self._process_line(line, is_main))
            elif line.lower().startswith('t.ex. ') and self.example_sentence_pattern.match(line[6:]):
                example = line[6:].strip()
                processed_lines.append(self._apply_color_styling(example, is_gray=True))
            elif line.startswith('t.ex. ') and self.example_sentence_pattern.match(line[6:]):
                example = line[6:].strip()
                processed_lines.append(self._apply_color_styling(example, is_gray=True))
            else:
                if self.example_sentence_pattern.match(line):
                    next_idx = idx + 1
                    if next_idx < len(lines):
                        next_line = lines[next_idx].strip()
                        if next_line and re.match(r'^ordet\s+anv\w*\b', next_line, flags=re.IGNORECASE):
                            example = self._remove_wrapping_parentheses(line)
                            combined = f'{example}<br>{next_line}'
                            processed_lines.append(self._apply_color_styling(combined, is_gray=True))
                            idx += 2
                            continue

                processed_lines.append(self._process_line(line, is_main))

            idx += 1

        return '<br>'.join(processed_lines)

    def _normalize_gray_span_styles(self, text: str) -> str:
        def is_gray_value(value: str) -> bool:
            if not value:
                return False
            if re.fullmatch(r'#c2c2c2', value.strip(), flags=re.IGNORECASE):
                return True
            if re.fullmatch(
                r'rgb\(\s*194\s*,\s*194\s*,\s*194\s*\)\s*;?',
                value.strip(),
                flags=re.IGNORECASE,
            ):
                return True
            return False

        def normalize_style(style: str) -> str:
            m = re.search(r'color\s*:\s*([^;]+)\s*;?', style, flags=re.IGNORECASE)
            if not m:
                return style

            color_value = m.group(1).strip()
            if is_gray_value(color_value):
                return style

            without_color = re.sub(r'color\s*:\s*[^;]+\s*;?', '', style, flags=re.IGNORECASE).strip()
            without_color = re.sub(r';\s*;', ';', without_color)
            without_color = without_color.strip(' ;')

            if without_color:
                return f'color: rgb(194, 194, 194); {without_color}'
            return 'color: rgb(194, 194, 194)'

        def repl_double(m: re.Match) -> str:
            before = m.group(1)
            style = m.group(2)
            after = m.group(3)
            new_style = normalize_style(style)
            return f'<span{before}style="{new_style}"{after}>'

        def repl_single(m: re.Match) -> str:
            before = m.group(1)
            style = m.group(2)
            after = m.group(3)
            new_style = normalize_style(style)
            return f"<span{before}style='{new_style}'{after}>"

        text = re.sub(r'<span\b([^>]*?)\bstyle="([^"]*)"([^>]*)>', repl_double, text, flags=re.IGNORECASE)
        text = re.sub(r"<span\b([^>]*?)\bstyle='([^']*)'([^>]*)>", repl_single, text, flags=re.IGNORECASE)
        return text

    def _maybe_split_gray_span_on_double_break(self, span_html: str, allow_split: bool) -> str:
        if not allow_split:
            return span_html

        open_tag = self._gray_span_open_tag(span_html)
        if not open_tag:
            return span_html

        if not span_html.endswith('</span>'):
            return span_html

        inner = span_html[len(open_tag):-len('</span>')]

        if '<br><br>' not in inner:
            return span_html

        left, right = inner.split('<br><br>', 1)
        right_stripped = right.strip()
        if not right_stripped.startswith('('):
            return span_html

        left = left.strip()
        right = right_stripped
        return (
            f'{open_tag}{left}</span>'
            f'<br><br>'
            f'{open_tag}{right}</span>'
        )
    
    def _process_or_separators_and_number(self, content: str, is_main: bool) -> str:
        """Process Or, separators and add numbering"""
        # Split by <br><br> or <br>Or, to get individual definitions
        definitions = []
        
        # First, split by double <br> which indicates separate definitions
        parts = re.split(r'<br>\s*<br>', content)
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # Check if this part has "Or, " separators
            if '<br>Or, ' in part or '<br>Or,' in part:
                # Split by Or, and add each as a separate definition
                or_parts = re.split(r'<br>\s*Or,?\s*', part)
                for or_part in or_parts:
                    or_part = or_part.strip()
                    if or_part:
                        definitions.append(or_part)
            else:
                definitions.append(part)
        
        # If we have multiple definitions and this is the main definition, add numbering
        if len(definitions) > 1 and is_main:
            numbered_defs = []
            for i, definition in enumerate(definitions, 1):
                numbered_defs.append(f"{i}. {definition}")
            return '<br><br>'.join(numbered_defs)
        
        # Otherwise, just join with double <br>
        return '<br><br>'.join(definitions)

    def _process_line(self, line: str, is_main_definition: bool) -> str:
        """Process a single line and apply appropriate styling"""
        if line.startswith('<span') and line.endswith('</span>') and self._gray_span_open_tag(line):
            return self._apply_color_styling(line, is_gray=True)

        if self.example_sentence_pattern.match(line):
            line = self._normalize_quoted_example_lines(line)
            return self._apply_color_styling(line, is_gray=True)

        maybe_unwrapped = self._remove_wrapping_parentheses(line)
        if maybe_unwrapped != line and self.example_sentence_pattern.match(maybe_unwrapped.strip()):
            line = self._normalize_quoted_example_lines(maybe_unwrapped)
            return self._apply_color_styling(line, is_gray=True)

        if self._is_synonym_or_extra(line):
            return self._apply_color_styling(line, is_gray=True)

        return self._apply_color_styling(line, is_gray=False)

    def _remove_wrapping_parentheses(self, line: str) -> str:
        """Remove parentheses that wrap the entire line"""
        match = self.parentheses_example_pattern.match(line)
        if match:
            return match.group(1)
        return line

    def _normalize_quoted_example_lines(self, html_text: str) -> str:
        parts = html_text.split('<br>')
        out = []
        for part in parts:
            stripped = part.strip()
            if not stripped:
                out.append(part)
                continue

            normalized = part
            if stripped.startswith('(') and stripped.endswith(')'):
                unwrapped = self._remove_wrapping_parentheses(stripped)
                if unwrapped.strip().startswith(('"', "'")):
                    normalized = unwrapped

            lead = normalized.strip()
            if lead.startswith(('"', "'")):
                normalized = re.sub(r'"\s*,\s*$', '"', normalized)
                normalized = re.sub(r"'\s*,\s*$", "'", normalized)
                normalized = re.sub(r'"\s*\)\s*$', '"', normalized)
                normalized = re.sub(r"'\s*\)\s*$", "'", normalized)
                normalized = re.sub(r'"\s*,\s*"', '"<br>"', normalized)
                normalized = re.sub(r"'\s*,\s*'", "'<br>'", normalized)

            out.append(normalized)

        return '<br>'.join(out)

    def _is_synonym_or_extra(self, line: str) -> bool:
        """Check if a line is a synonym or extra information"""
        # Check for common patterns
        synonym_patterns = [
            r'^\(syn:',          # (syn: something)
            r'^\(best:',         # (best: something)
            r'^\(pl:',           # (pl: something)
            r'^\(på',            # (på something: something)
            r'^\(en [^)]+:',     # (en something: English translation)
            r'^\(ett [^)]+:',    # (ett something: English translation)
            r'^\(ett [^)]+\)',   # (ett something) without colon
            r'^\(en [^)]+\)',    # (en something) without colon
        ]
        
        for pattern in synonym_patterns:
            if re.match(pattern, line, re.IGNORECASE):
                return True
        
        return False

    def _apply_color_styling(self, line: str, is_gray: bool) -> str:
        """Apply color styling to a line"""
        if not is_gray:
            return line

        if line.startswith('<span') and line.endswith('</span>'):
            open_tag = self._gray_span_open_tag(line)
            if open_tag:
                inner = line[len(open_tag):-len('</span>')]
                inner = self._normalize_quoted_example_lines(inner)
                inner = self._italicize_current_terms(inner)
                return f'{open_tag}{inner}</span>'

        line = self._normalize_quoted_example_lines(line)
        line = self._italicize_current_terms(line)
        return self._wrap_gray_span(line)

    def _convert_rgb_to_hex(self, text: str) -> str:
        """Convert rgb(r, g, b) colors to hex format"""
        def rgb_to_hex(match):
            r, g, b = map(int, match.groups())
            return f"#{r:02X}{g:02X}{b:02X}"
        
        return self.rgb_pattern.sub(rgb_to_hex, text)


class WebServer(BaseHTTPRequestHandler):
    """HTTP server to handle web interface requests"""

    cleaner = None

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
            if isinstance(e, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                return
            print(f"Error handling GET {path}: {e}")
            traceback.print_exc()
            if path.startswith("/api/"):
                try:
                    self.send_json_error(500, str(e))
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
            else:
                try:
                    self.send_error(500, str(e))
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

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
            else:
                self.send_error(404)
        except Exception as e:
            if isinstance(e, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                return
            print(f"Error handling POST {path}: {e}")
            traceback.print_exc()
            if path.startswith("/api/"):
                try:
                    self.send_json_error(500, str(e))
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
            else:
                try:
                    self.send_error(500, str(e))
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

    def serve_interface(self):
        """Serve the main HTML interface"""
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
            if not self.cleaner:
                raise Exception("Cleaner not initialized")

            decks = self.cleaner.anki.get_deck_names()
            response = {"decks": decks}
            self.send_json_response(response)
        except Exception as e:
            print(f"Error getting decks: {e}")
            self.send_json_error(500, str(e))

    def serve_status(self):
        """Serve server status"""
        response = {
            "status": "running",
            "anki_connected": False,
        }

        # Test Anki connection
        try:
            if self.cleaner:
                self.cleaner.anki.get_deck_names()
                response["anki_connected"] = True
        except Exception as e:
            print(f"Anki connection failed: {e}")

        self.send_json_response(response)

    def handle_process_request(self, data):
        """Handle card processing request"""
        try:
            deck_name = data.get("deck_name")
            batch_size = data.get("batch_size", 25)
            start_from = data.get("start_from", 0)

            if not deck_name:
                raise Exception("deck_name is required")

            if not self.cleaner:
                raise Exception("Cleaner not initialized")

            results = self.cleaner.process_cards_for_review(deck_name, batch_size, start_from)
            self.send_json_response(results)

        except Exception as e:
            print(f"Error in handle_process_request: {e}")
            traceback.print_exc()
            self.send_json_error(500, str(e))

    def handle_apply_request(self, data):
        """Handle apply changes request"""
        try:
            if not self.cleaner:
                raise Exception("Cleaner not initialized")

            results = self.cleaner.apply_selected_changes(data)
            self.send_json_response(results)

        except Exception as e:
            self.send_json_error(500, str(e))

    def send_json_error(self, status_code: int, message: str):
        """Send a JSON error response"""
        response_data = json.dumps({"error": message}, ensure_ascii=False, indent=2)
        response_bytes = response_data.encode("utf-8")

        try:
            self.send_response(status_code)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

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
            if isinstance(e, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                return
            print(f"Error serializing JSON response: {e}")
            traceback.print_exc()
            try:
                error_response = json.dumps(
                    {"error": f"JSON serialization failed: {str(e)}"}
                )
                self.send_response(500)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(error_response)))
                self.end_headers()
                self.wfile.write(error_response.encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        """Override to reduce log noise"""
        pass

    def get_interface_html(self):
        """Get the HTML interface content"""
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Anki Deck Cleaner</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0b0f14; color: #e5e7eb; min-height: 100vh; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; background: #0f172a; border-radius: 10px; box-shadow: 0 2px 24px rgba(0,0,0,0.45); overflow: hidden; border: 1px solid #1f2937; }
        .header { background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%); color: white; padding: 30px; text-align: center; }
        .header h1 { font-size: 2.5rem; margin-bottom: 10px; font-weight: 300; }
        .header p { opacity: 0.9; font-size: 1.1rem; }
        .controls { padding: 20px 30px; background: #0b1220; border-bottom: 1px solid #1f2937; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; }
        .control-group { display: flex; align-items: center; gap: 15px; }
        .btn { padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; font-size: 14px; font-weight: 500; transition: all 0.3s ease; text-decoration: none; display: inline-flex; align-items: center; gap: 8px; }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .btn-primary { background: #4f46e5; color: white; }
        .btn-primary:hover:not(:disabled) { background: #4338ca; transform: translateY(-1px); }
        .btn-secondary { background: #334155; color: white; }
        .btn-success { background: #16a34a; color: white; }
        .btn-success:hover:not(:disabled) { background: #15803d; }
        .main-content { padding: 30px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; font-weight: 600; color: #cbd5e1; }
        .form-control { width: 100%; padding: 10px; border: 1px solid #334155; border-radius: 5px; font-size: 14px; background: #0b1220; color: #e5e7eb; }
        .form-control:focus { outline: none; border-color: #4f46e5; box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.2); }
        .status-indicator { padding: 8px 16px; border-radius: 20px; font-size: 12px; font-weight: 600; text-transform: uppercase; }
        .status-connected { background: rgba(22, 163, 74, 0.15); color: #86efac; border: 1px solid rgba(22, 163, 74, 0.35); }
        .status-disconnected { background: rgba(239, 68, 68, 0.12); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.35); }
        .processing { display: none; text-align: center; padding: 40px; }
        .processing-spinner { width: 40px; height: 40px; border: 4px solid #1f2937; border-top: 4px solid #4f46e5; border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 20px; }
        .progress-track { height: 10px; background: #1f2937; border-radius: 9999px; overflow: hidden; margin: 18px auto 0; max-width: 520px; }
        .progress-fill { height: 100%; width: 0%; background: linear-gradient(90deg, #4f46e5 0%, #7c3aed 100%); transition: width 0.2s ease; }
        .progress-meta { margin-top: 10px; color: #94a3b8; font-size: 13px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .card { background: #0b1220; border: 1px solid #1f2937; border-radius: 8px; margin-bottom: 20px; overflow: hidden; transition: all 0.3s ease; }
        .card:hover { box-shadow: 0 6px 20px rgba(0,0,0,0.35); }
        .card.selected { border-color: rgba(79, 70, 229, 0.7); box-shadow: 0 6px 20px rgba(79, 70, 229, 0.2); }
        .card-header { background: #0f172a; padding: 15px; border-bottom: 1px solid #1f2937; display: flex; align-items: center; justify-content: space-between; }
        .card-title { font-size: 1.1rem; font-weight: 600; color: #e5e7eb; display: flex; align-items: center; gap: 15px; }
        .checkbox-wrapper { display: flex; align-items: center; gap: 10px; }
        .custom-checkbox { width: 20px; height: 20px; border: 2px solid #334155; border-radius: 4px; cursor: pointer; transition: all 0.3s ease; display: flex; align-items: center; justify-content: center; }
        .custom-checkbox.checked { background: #4f46e5; border-color: #4f46e5; color: white; }
        .card-body { padding: 20px; }
        .field-group { margin-bottom: 20px; }
        .field-group:last-child { margin-bottom: 0; }
        .field-group.dimmed { opacity: 0.55; }
        .field-label { font-weight: 600; color: #cbd5e1; margin-bottom: 10px; display: block; }
        .field-comparison { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .field-section { background: #0f172a; border: 1px solid #1f2937; border-radius: 8px; padding: 15px; }
        .field-section h4 { color: #94a3b8; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; }
        .field-subtitle { color: #94a3b8; font-size: 12px; font-weight: 600; margin-top: 12px; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.6px; }
        .field-raw { background: #0b1220; border: 1px solid #243244; border-radius: 8px; padding: 12px; font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; line-height: 1.4; white-space: pre-wrap; word-break: break-word; color: #cbd5e1; }
        .field-rendered { background: #0b1220; border: 2px solid #64748b; border-radius: 8px; padding: 12px; font-size: 14px; line-height: 1.5; word-break: break-word; color: #e5e7eb; }
        .field-input { width: 100%; min-height: 120px; padding: 10px; border: 1px solid #334155; border-radius: 8px; font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; resize: vertical; background: #0b1220; color: #e5e7eb; }
        .field-input:focus { outline: none; border-color: #4f46e5; box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.2); }
        .stats { display: flex; gap: 20px; align-items: center; font-weight: 500; color: #cbd5e1; }
        .stat-item { display: flex; align-items: center; gap: 8px; }
        .empty-state { text-align: center; padding: 60px 20px; color: #94a3b8; }
        .diff-container { font-family: 'Consolas', 'Monaco', monospace; font-size: 14px; line-height: 1.5; }
        .diff-split { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; border: 1px solid #1f2937; border-radius: 4px; overflow: hidden; }
        .diff-left, .diff-right { background: #0f172a; display: flex; flex-direction: column; }
        .diff-header { background: #0b1220; padding: 8px; font-weight: 600; font-size: 12px; color: #cbd5e1; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #1f2937; }
        .diff-content { padding: 10px; font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; line-height: 1.4; white-space: pre-wrap; word-wrap: break-word; overflow-y: auto; color: #e5e7eb; }
        .diff-added { background-color: rgba(22, 163, 74, 0.15); color: #86efac; text-decoration: none; }
        .diff-removed { background-color: rgba(239, 68, 68, 0.12); color: #fca5a5; text-decoration: line-through; }
        .diff-unchanged { color: #94a3b8; }
        @media (max-width: 768px) { .diff-split { grid-template-columns: 1fr; } .field-comparison { grid-template-columns: 1fr; } }
        .preview-box { background: #0b1220; color: #e5e7eb; padding: 12px; border-radius: 8px; margin-top: 10px; border: 1px solid #243244; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Anki Deck Cleaner</h1>
            <p>Clean up your Swedish flashcards with automated formatting rules</p>
        </div>

        <div class="controls" id="mainControls">
            <div class="control-group">
                <div class="status-indicator" id="statusIndicator">Connecting...</div>
            </div>
            <div class="stats" id="statsDisplay" style="display: none;">
                <div class="stat-item"><span>✅</span><span>Selected: <span id="selectedCards">0</span>/<span id="totalCards">0</span></span></div>
                <div class="stat-item"><span>⏭️</span><span>Skipped: <span id="skippedCards">0</span></span></div>
                <div class="stat-item"><span>⏱️</span><span>Scan: <span id="scanSeconds">0</span>s</span></div>
            </div>
            <div class="control-group" id="actionControls" style="display: none;">
                <button class="btn btn-secondary" onclick="selectAll()">Select All</button>
                <button class="btn btn-secondary" onclick="selectNone()">Select None</button>
                <button class="btn btn-success" onclick="applyChanges()" id="applyBtn">Apply Changes</button>
            </div>
        </div>

        <div class="main-content">
            <div id="deckSelector">
                <div class="form-group">
                    <label for="deckSelect">Select Deck:</label>
                    <select id="deckSelect" class="form-control"></select>
                </div>
                <div class="form-group">
                    <label for="batchSize">Batch Size:</label>
                    <input type="number" id="batchSize" class="form-control" value="25" min="1" max="1000">
                </div>
                <div class="form-group">
                    <label for="startOffset">Start Offset:</label>
                    <input type="number" id="startOffset" class="form-control" value="0" min="0" step="1">
                </div>
                <div class="form-group">
                    <button class="btn btn-primary" onclick="processCards()" id="processBtn" style="width: 100%;">Process Cards</button>
                </div>
            </div>

            <div class="processing" id="processing">
                <div class="processing-spinner"></div>
                <p id="processingText">Processing cards...</p>
                <div class="progress-track"><div class="progress-fill" id="progressFill"></div></div>
                <div class="progress-meta" id="progressMeta"></div>
            </div>

            <div id="cardContainer" style="display: none;">
                <!-- Cards will be generated here -->
            </div>

            <div class="empty-state" id="emptyState" style="display: none;">
                <div style="font-size: 4rem; margin-bottom: 20px; opacity: 0.5;">📝</div>
                <h3>No cards to review</h3>
                <p>Select a deck and click "Process Cards" to get started</p>
            </div>
        </div>
    </div>

    <script>
        let cardData = [];
        let selectedCards = new Set();
        let skippedCount = 0;
        let currentDeckName = '';
        let lastScanSeconds = 0;

        document.addEventListener('DOMContentLoaded', function() {
            checkStatus();
            loadDecks();
        });

        function escapeHtml(str) {
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        function renderRawDiff(beforeText, afterText, side) {
            const a = String(beforeText ?? '');
            const b = String(afterText ?? '');
            if (a === b) {
                return `<span class="diff-unchanged">${escapeHtml(a)}</span>`;
            }

            const tokenRe = /(<[^>]+>|&[^;\\s]+;|\\s+|[^<&\\s]+)/g;
            const aTokens = a.match(tokenRe) || [];
            const bTokens = b.match(tokenRe) || [];

            function myersDiff(A, B) {
                const N = A.length;
                const M = B.length;
                const max = N + M;
                const offset = max;

                let v = new Array(2 * max + 1).fill(0);
                const trace = [];

                for (let d = 0; d <= max; d++) {
                    const vNew = v.slice();
                    for (let k = -d; k <= d; k += 2) {
                        const kIdx = k + offset;
                        let x;
                        if (k === -d || (k !== d && v[kIdx - 1] < v[kIdx + 1])) {
                            x = v[kIdx + 1];
                        } else {
                            x = v[kIdx - 1] + 1;
                        }

                        let y = x - k;
                        while (x < N && y < M && A[x] === B[y]) {
                            x++;
                            y++;
                        }

                        vNew[kIdx] = x;
                        if (x >= N && y >= M) {
                            trace.push(vNew.slice());
                            return backtrack(trace, A, B, offset);
                        }
                    }

                    trace.push(vNew.slice());
                    v = vNew;
                }

                return backtrack(trace, A, B, offset);
            }

            function backtrack(trace, A, B, offset) {
                let x = A.length;
                let y = B.length;
                const ops = [];

                for (let d = trace.length - 1; d >= 0; d--) {
                    const v = trace[d];
                    const k = x - y;

                    let prevK;
                    if (k === -d || (k !== d && v[k - 1 + offset] < v[k + 1 + offset])) {
                        prevK = k + 1;
                    } else {
                        prevK = k - 1;
                    }

                    const prevX = d === 0 ? 0 : v[prevK + offset];
                    const prevY = prevX - prevK;

                    while (x > prevX && y > prevY) {
                        ops.push({ t: 'equal', v: A[x - 1] });
                        x--;
                        y--;
                    }

                    if (d === 0) {
                        break;
                    }

                    if (x === prevX) {
                        ops.push({ t: 'insert', v: B[prevY] });
                    } else {
                        ops.push({ t: 'delete', v: A[prevX] });
                    }

                    x = prevX;
                    y = prevY;
                }

                while (x > 0 && y > 0) {
                    ops.push({ t: 'equal', v: A[x - 1] });
                    x--;
                    y--;
                }
                while (x > 0) {
                    ops.push({ t: 'delete', v: A[x - 1] });
                    x--;
                }
                while (y > 0) {
                    ops.push({ t: 'insert', v: B[y - 1] });
                    y--;
                }

                ops.reverse();
                const merged = [];
                for (const op of ops) {
                    const last = merged[merged.length - 1];
                    if (last && last.t === op.t) {
                        last.v += op.v;
                    } else {
                        merged.push({ t: op.t, v: op.v });
                    }
                }
                return merged;
            }

            const diffOps = myersDiff(aTokens, bTokens);
            const out = [];
            for (const op of diffOps) {
                if (op.t === 'equal') {
                    out.push(`<span class="diff-unchanged">${escapeHtml(op.v)}</span>`);
                } else if (side === 'before' && op.t === 'delete') {
                    out.push(`<span class="diff-removed">${escapeHtml(op.v)}</span>`);
                } else if (side === 'after' && op.t === 'insert') {
                    out.push(`<span class="diff-added">${escapeHtml(op.v)}</span>`);
                }
            }

            return out.join('');
        }

        async function checkStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                
                const indicator = document.getElementById('statusIndicator');
                if (data.anki_connected) {
                    indicator.textContent = 'Connected';
                    indicator.className = 'status-indicator status-connected';
                } else {
                    indicator.textContent = 'Disconnected';
                    indicator.className = 'status-indicator status-disconnected';
                }
            } catch (error) {
                console.error('Status check failed:', error);
            }
        }

        async function loadDecks() {
            try {
                const response = await fetch('/api/decks');
                const data = await response.json();
                
                const select = document.getElementById('deckSelect');
                select.innerHTML = '<option value="">Select a deck...</option>';
                
                data.decks.forEach(deck => {
                    const option = document.createElement('option');
                    option.value = deck;
                    option.textContent = deck;
                    select.appendChild(option);
                });

                if (Array.isArray(data.decks) && data.decks.includes('Default')) {
                    select.value = 'Default';
                }

                const deckName = select.value;
                if (deckName) {
                    const saved = localStorage.getItem(`startOffset:${deckName}`);
                    if (saved !== null) {
                        document.getElementById('startOffset').value = saved;
                    }
                }

                select.addEventListener('change', () => {
                    const dn = select.value;
                    const savedOffset = localStorage.getItem(`startOffset:${dn}`);
                    document.getElementById('startOffset').value = savedOffset !== null ? savedOffset : '0';
                });
            } catch (error) {
                console.error('Failed to load decks:', error);
            }
        }

        async function processCards() {
            const deckName = document.getElementById('deckSelect').value;
            const batchSize = parseInt(document.getElementById('batchSize').value);
            const initialOffset = parseInt(document.getElementById('startOffset').value) || 0;
            
            if (!deckName) {
                alert('Please select a deck');
                return;
            }
            
            currentDeckName = deckName;
            
            document.getElementById('deckSelector').style.display = 'none';
            document.getElementById('processing').style.display = 'block';
            document.getElementById('processingText').textContent = 'Processing cards...';
            document.getElementById('progressFill').style.width = '0%';
            document.getElementById('progressMeta').textContent = '';
            
            try {
                const targetCount = Math.max(1, batchSize || 25);
                const scanChunkSize = Math.min(100, targetCount);
                const startTime = performance.now();

                let startFrom = Math.max(0, initialOffset);
                let totalCards = null;
                let combined = [];
                let totalSkipped = 0;

                while (combined.length < targetCount) {
                    const response = await fetch('/api/process', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            deck_name: deckName,
                            batch_size: scanChunkSize,
                            start_from: startFrom
                        })
                    });

                    const data = await response.json();

                    if (data.error) {
                        throw new Error(data.error);
                    }

                    if (totalCards === null && typeof data.total_cards === 'number') {
                        totalCards = data.total_cards;
                    }

                    if (Array.isArray(data.cards)) {
                        combined = combined.concat(data.cards);
                    }

                    totalSkipped += (data.skipped_count || 0);

                    startFrom = (typeof data.next_start_from === 'number')
                        ? data.next_start_from
                        : (startFrom + (data.processed_count || 0));

                    const scanned = startFrom;
                    const total = (totalCards === null) ? scanned : totalCards;
                    const pct = total > 0 ? Math.min(100, Math.round((scanned / total) * 100)) : 0;

                    const elapsedSeconds = Math.max(0.001, (performance.now() - startTime) / 1000);
                    const rate = scanned / elapsedSeconds;

                    document.getElementById('processingText').textContent = `Scanning ${scanned}/${total} cards... Found ${combined.length} to review`;
                    document.getElementById('progressFill').style.width = `${pct}%`;
                    document.getElementById('progressMeta').textContent = `${rate.toFixed(1)} cards/sec`;

                    if (!data.processed_count || data.processed_count <= 0) {
                        break;
                    }
                    if (totalCards !== null && startFrom >= totalCards) {
                        break;
                    }
                }

                const durationSeconds = (performance.now() - startTime) / 1000;

                localStorage.setItem(`startOffset:${deckName}`, String(startFrom));
                document.getElementById('startOffset').value = String(startFrom);

                displayCards({
                    cards: combined.slice(0, targetCount),
                    skipped_count: totalSkipped,
                    total_cards: totalCards === null ? 0 : totalCards,
                    processed_count: startFrom,
                    start_from: 0,
                    scan_seconds: durationSeconds
                });
                
            } catch (error) {
                console.error('Processing failed:', error);
                alert('Processing failed: ' + error.message);
                document.getElementById('deckSelector').style.display = 'block';
                document.getElementById('processing').style.display = 'none';
            }
        }

        function displayCards(data) {
            cardData = data.cards || [];
            selectedCards.clear();
            skippedCount = data.skipped_count || 0;
            lastScanSeconds = data.scan_seconds || 0;

            cardData.forEach(card => selectedCards.add(card.card_id));
            
            document.getElementById('processing').style.display = 'none';
            
            if (cardData.length === 0) {
                document.getElementById('emptyState').style.display = 'block';
                document.getElementById('statsDisplay').style.display = 'none';
                document.getElementById('actionControls').style.display = 'none';
                return;
            }
            
            document.getElementById('cardContainer').style.display = 'block';
            document.getElementById('statsDisplay').style.display = 'flex';
            document.getElementById('actionControls').style.display = 'flex';
            
            updateStats();
            renderCards();
        }

        function renderCards() {
            const container = document.getElementById('cardContainer');
            container.innerHTML = '';
            
            cardData.forEach((card, index) => {
                const cardEl = createCardElement(card, index);
                container.appendChild(cardEl);
            });
        }

        function createCardElement(card, index) {
            const cardDiv = document.createElement('div');
            cardDiv.className = 'card';
            cardDiv.id = `card-${index}`;
            
            const isSelected = selectedCards.has(card.card_id);
            if (isSelected) {
                cardDiv.classList.add('selected');
            }

            const originalFront = card.original_front || '';
            const newFront = card.new_front || '';
            const originalBack = card.original_back || '';
            const newBack = card.new_back || '';

            const frontChanged = originalFront !== newFront;
            const backChanged = originalBack !== newBack;
            
            cardDiv.innerHTML = `
                <div class="card-header">
                    <div class="card-title">
                        <div class="checkbox-wrapper">
                            <div class="custom-checkbox ${isSelected ? 'checked' : ''}" onclick="toggleCard(${card.card_id})">
                                ${isSelected ? '✓' : ''}
                            </div>
                        </div>
                        Card ID: ${card.card_id}
                    </div>
                </div>
                <div class="card-body">
                    <div class="field-group ${frontChanged ? '' : 'dimmed'}">
                        <label class="field-label">Front Field</label>
                        <div class="field-comparison">
                            <div class="field-section">
                                <h4>Before</h4>
                                <div class="field-subtitle">Rendered</div>
                                <div class="field-rendered">${originalFront}</div>
                                <div class="field-subtitle">Raw HTML</div>
                                <pre class="field-raw">${renderRawDiff(originalFront, newFront, 'before')}</pre>
                            </div>
                            <div class="field-section">
                                <h4>After</h4>
                                <div class="field-subtitle">Rendered</div>
                                <div class="field-rendered">${newFront}</div>
                                <div class="field-subtitle">Raw HTML</div>
                                <pre class="field-raw">${renderRawDiff(originalFront, newFront, 'after')}</pre>
                            </div>
                        </div>
                    </div>
                    <div class="field-group ${backChanged ? '' : 'dimmed'}">
                        <label class="field-label">Back Field</label>
                        <div class="field-comparison">
                            <div class="field-section">
                                <h4>Before</h4>
                                <div class="field-subtitle">Rendered</div>
                                <div class="field-rendered">${originalBack}</div>
                                <div class="field-subtitle">Raw HTML</div>
                                <pre class="field-raw" id="diff-before-${card.card_id}">${renderRawDiff(originalBack, newBack, 'before')}</pre>
                            </div>
                            <div class="field-section">
                                <h4>After</h4>
                                <div class="field-subtitle">Rendered</div>
                                <div class="field-rendered" id="render-edit-${card.card_id}"></div>
                                <div class="field-subtitle">Raw HTML (Editable)</div>
                                <textarea class="field-input" id="edit-${card.card_id}" rows="6">${newBack}</textarea>
                                <div class="field-subtitle">Raw HTML (Diff)</div>
                                <pre class="field-raw" id="diff-edit-${card.card_id}"></pre>
                            </div>
                        </div>
                    </div>
                </div>
            `;

            const textarea = cardDiv.querySelector(`#edit-${card.card_id}`);
            const rendered = cardDiv.querySelector(`#render-edit-${card.card_id}`);
            const rawDiff = cardDiv.querySelector(`#diff-edit-${card.card_id}`);
            const rawBeforeDiff = cardDiv.querySelector(`#diff-before-${card.card_id}`);
            if (textarea && rendered) {
                rendered.innerHTML = textarea.value || '';
                if (rawDiff) {
                    rawDiff.innerHTML = renderRawDiff(originalBack, textarea.value || '', 'after');
                }
                if (rawBeforeDiff) {
                    rawBeforeDiff.innerHTML = renderRawDiff(originalBack, textarea.value || '', 'before');
                }
                textarea.addEventListener('input', () => {
                    rendered.innerHTML = textarea.value || '';
                    if (rawDiff) {
                        rawDiff.innerHTML = renderRawDiff(originalBack, textarea.value || '', 'after');
                    }
                    if (rawBeforeDiff) {
                        rawBeforeDiff.innerHTML = renderRawDiff(originalBack, textarea.value || '', 'before');
                    }
                });
            }
            
            return cardDiv;
        }

        function toggleCard(cardId) {
            if (selectedCards.has(cardId)) {
                selectedCards.delete(cardId);
            } else {
                selectedCards.add(cardId);
            }
            
            const cardEl = document.getElementById(`card-${cardData.findIndex(c => c.card_id === cardId)}`);
            const checkbox = cardEl.querySelector('.custom-checkbox');
            
            if (selectedCards.has(cardId)) {
                cardEl.classList.add('selected');
                checkbox.classList.add('checked');
                checkbox.textContent = '✓';
            } else {
                cardEl.classList.remove('selected');
                checkbox.classList.remove('checked');
                checkbox.textContent = '';
            }
            
            updateStats();
        }

        function selectAll() {
            cardData.forEach(card => selectedCards.add(card.card_id));
            renderCards();
            updateStats();
        }

        function selectNone() {
            selectedCards.clear();
            renderCards();
            updateStats();
        }

        function updateStats() {
            document.getElementById('selectedCards').textContent = selectedCards.size;
            document.getElementById('totalCards').textContent = cardData.length;
            document.getElementById('skippedCards').textContent = skippedCount;
            document.getElementById('scanSeconds').textContent = (lastScanSeconds || 0).toFixed(2);
            
            const applyBtn = document.getElementById('applyBtn');
            applyBtn.disabled = selectedCards.size === 0;
        }

        async function applyChanges() {
            if (selectedCards.size === 0) {
                alert('No cards selected');
                return;
            }
            
            const updates = [];
            
            selectedCards.forEach(cardId => {
                const card = cardData.find(c => c.card_id === cardId);
                if (card) {
                    const textarea = document.getElementById(`edit-${cardId}`);
                    updates.push({
                        card_id: cardId,
                        note_id: card.note_id,
                        front_field: card.front_field,
                        back_field: card.back_field,
                        front: card.new_front,
                        back: textarea.value
                    });
                }
            });
            
            document.getElementById('applyBtn').disabled = true;
            document.getElementById('applyBtn').textContent = 'Applying...';
            
            try {
                const response = await fetch('/api/apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ updates })
                });
                
                const data = await response.json();
                
                if (data.error) {
                    throw new Error(data.error);
                }
                
                // Reset UI
                document.getElementById('cardContainer').style.display = 'none';
                document.getElementById('deckSelector').style.display = 'block';
                document.getElementById('statsDisplay').style.display = 'none';
                document.getElementById('actionControls').style.display = 'none';
                
            } catch (error) {
                console.error('Apply failed:', error);
                alert('Apply failed: ' + error.message);
            } finally {
                document.getElementById('applyBtn').disabled = false;
                document.getElementById('applyBtn').textContent = 'Apply Changes';
            }
        }
    </script>
</body>
</html>"""


class AnkiDeckCleaner:
    """Main application class for cleaning Anki decks"""

    def __init__(self):
        self.anki = AnkiConnector()
        self.card_cleaner = CardCleaner()
        self._deck_card_ids_cache = {}

    def _build_prioritized_card_ids(self, deck_name: str) -> List[int]:
        phase1_query = f'deck:"{deck_name}" -is:due -is:suspended'
        phase2_query = f'deck:"{deck_name}" (is:due OR is:suspended)'

        phase1_ids = self.anki.find_cards(phase1_query) or []
        phase2_ids = self.anki.find_cards(phase2_query) or []

        all_ids = list(dict.fromkeys((phase1_ids + phase2_ids)))
        if not all_ids:
            return []

        info_by_id = {}
        chunk_size = 500
        for i in range(0, len(all_ids), chunk_size):
            chunk = all_ids[i:i + chunk_size]
            infos = self.anki.get_card_info(chunk) or []
            for info in infos:
                cid = info.get('cardId') or info.get('card_id') or info.get('id')
                if cid is not None:
                    info_by_id[cid] = info

        def due_key(cid: int) -> int:
            v = info_by_id.get(cid, {}).get('due')
            return 0 if v is None else int(v)

        def reps_key(cid: int) -> int:
            v = info_by_id.get(cid, {}).get('reps')
            return 0 if v is None else int(v)

        phase1_sorted = sorted(phase1_ids, key=lambda cid: (due_key(cid), cid))
        phase2_sorted = sorted(phase2_ids, key=lambda cid: (reps_key(cid), due_key(cid), cid))

        seen = set()
        combined = []
        for cid in phase1_sorted:
            if cid not in seen:
                seen.add(cid)
                combined.append(cid)
        for cid in phase2_sorted:
            if cid not in seen:
                seen.add(cid)
                combined.append(cid)

        return combined

    def process_cards_for_review(self, deck_name: str, batch_size: int = 25, start_from: int = 0) -> Dict:
        """Process cards and return those that need changes"""
        # Get all cards in deck
        card_ids = self._deck_card_ids_cache.get(deck_name)
        if start_from == 0 or card_ids is None:
            card_ids = self._build_prioritized_card_ids(deck_name)
            self._deck_card_ids_cache[deck_name] = card_ids
        
        # Apply batch and start_from
        end_idx = min(start_from + batch_size, len(card_ids))
        batch_card_ids = card_ids[start_from:end_idx]
        
        if not batch_card_ids:
            return {"cards": [], "skipped_count": 0, "total_cards": len(card_ids)}
        
        # Get card info
        cards_info = self.anki.get_card_info(batch_card_ids)

        card_entries = []
        note_ids_to_fetch = []
        for card_info in cards_info:
            note_id = (
                card_info.get('noteId')
                or card_info.get('note')
                or card_info.get('nid')
                or card_info.get('note_id')
            )
            card_entries.append((card_info, note_id))

            fields = card_info.get('fields')
            if not (isinstance(fields, dict) and len(fields) >= 2) and note_id is not None:
                note_ids_to_fetch.append(note_id)

        note_info_by_id = {}
        if note_ids_to_fetch:
            note_infos = self.anki.get_note_info(list(dict.fromkeys(note_ids_to_fetch)))
            for note_info in note_infos or []:
                nid = note_info.get('noteId') or note_info.get('note_id') or note_info.get('id')
                if nid is not None:
                    note_info_by_id[nid] = note_info
        
        cards_to_review = []
        skipped_count = 0
        
        def _extract_two_fields(fields_dict: Dict) -> Tuple[str, str, str, str]:
            items = []
            for name, meta in fields_dict.items():
                if isinstance(meta, dict):
                    order = meta.get('order')
                    value = meta.get('value', '')
                else:
                    order = None
                    value = '' if meta is None else str(meta)
                items.append((order if order is not None else 9999, name, value))
            items.sort(key=lambda t: t[0])
            if len(items) < 2:
                raise KeyError('fields')
            return items[0][1], items[1][1], items[0][2], items[1][2]

        for card_info, note_id in card_entries:
            if note_id is None:
                skipped_count += 1
                continue

            fields_dict = card_info.get('fields')
            if not (isinstance(fields_dict, dict) and len(fields_dict) >= 2):
                note_info = note_info_by_id.get(note_id)
                if not note_info:
                    skipped_count += 1
                    continue
                fields_dict = note_info.get('fields')

            if not (isinstance(fields_dict, dict) and len(fields_dict) >= 2):
                skipped_count += 1
                continue

            try:
                front_field, back_field, original_front, original_back = _extract_two_fields(fields_dict)
            except Exception:
                skipped_count += 1
                continue
            
            # Clean the card
            new_front, new_back, changed = self.card_cleaner.clean_card(original_front, original_back)
            
            if changed:
                card_id = (
                    card_info.get('cardId')
                    or card_info.get('card_id')
                    or card_info.get('id')
                )
                cards_to_review.append({
                    'card_id': card_id,
                    'note_id': note_id,
                    'front_field': front_field,
                    'back_field': back_field,
                    'original_front': original_front,
                    'original_back': original_back,
                    'new_front': new_front,
                    'new_back': new_back
                })
        
        return {
            "cards": cards_to_review,
            "skipped_count": skipped_count,
            "total_cards": len(card_ids),
            "processed_count": len(batch_card_ids),
            "start_from": start_from,
            "next_start_from": start_from + len(batch_card_ids)
        }

    def apply_selected_changes(self, data: Dict) -> Dict:
        """Apply the selected changes to cards"""
        updates = data.get('updates', [])
        updated_count = 0

        prepared = []
        
        for update in updates:
            try:
                note_id = update.get('note_id') or update.get('noteId') or update.get('nid') or update.get('note')
                if note_id is None:
                    raise KeyError('note_id')

                front_field = update.get('front_field') or update.get('frontField')
                back_field = update.get('back_field') or update.get('backField')
                if not front_field or not back_field:
                    note_infos = self.anki.get_note_info([note_id])
                    if not note_infos:
                        raise KeyError('front_field')

                    fields = note_infos[0].get('fields', {})
                    field_names = list(fields.keys())
                    if len(field_names) < 2:
                        raise KeyError('front_field')

                    front_field = front_field or field_names[0]
                    back_field = back_field or field_names[1]

                fields = {
                    front_field: update['front'],
                    back_field: update['back']
                }
                
                card_id = update.get('card_id') or update.get('cardId') or update.get('id')
                prepared.append((card_id, note_id, fields))
                
            except Exception as e:
                card_id = update.get('card_id') or update.get('cardId') or update.get('id')
                update_keys = list(update.keys()) if isinstance(update, dict) else []
                print(f"Failed to update card {card_id}: {e} (keys={update_keys})")

        chunk_size = 25
        for i in range(0, len(prepared), chunk_size):
            chunk = prepared[i:i + chunk_size]
            actions = [
                {
                    "action": "updateNoteFields",
                    "params": {"note": {"id": note_id, "fields": fields}},
                }
                for _, note_id, fields in chunk
            ]

            try:
                self.anki.multi(actions)
                updated_count += len(chunk)
            except Exception:
                for card_id, note_id, fields in chunk:
                    try:
                        self.anki.update_note_fields(note_id, fields)
                        updated_count += 1
                    except Exception as e:
                        print(f"Failed to update card {card_id}: {e} (keys=[])")
        
        return {"updated_count": updated_count, "total_updates": len(updates)}

    def run_server(self, port: int = 8766):
        """Run the web server"""
        WebServer.cleaner = self
        
        server = HTTPServer(('localhost', port), WebServer)
        
        print(f"Starting server on http://localhost:{port}")
        print("Press Ctrl+C to stop the server")
        
        # Open browser
        webbrowser.open(f'http://localhost:{port}')
        
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")
            server.shutdown()


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Anki Deck Cleaner')
    parser.add_argument('--port', type=int, default=8766, help='Port for web server (default: 8766)')
    
    args = parser.parse_args()
    
    cleaner = AnkiDeckCleaner()
    cleaner.run_server(args.port)


if __name__ == '__main__':
    main()
