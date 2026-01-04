from urllib.parse import unquote

with open("words.txt", "r", encoding="utf-8") as f:
    lines = [line.strip() for line in f if line.strip()]

words = []
# Parse Wiktionary URLs to extract words
for line in lines:
    if line.startswith("https://sv.wiktionary.org/wiki/"):
        # Extract word from Wiktionary URL and decode UTF-8
        encoded_word = line.split("/wiki/")[-1].split("#")[0]
        word = unquote(encoded_word, encoding="utf-8")
        words.append(word)
    else:
        print(f"Warning: Skipping non-Wiktionary URL: {line}")

with open("words.txt", "w", encoding="utf-8") as f:
    for i, word in enumerate(words):
        f.write(f"{word}{", " if i < len(words) - 1 else ""}")

print(f"Fixed up {len(words)} words")
