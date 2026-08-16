import json
import csv
import sys

# Usage: python merge_translations.py Game.json Game.locres_all.csv output.csv
json_path = sys.argv[1] if len(sys.argv) > 1 else "Game.json"
csv_path = sys.argv[2] if len(sys.argv) > 2 else "Game.locres_all.csv"
output_path = sys.argv[3] if len(sys.argv) > 3 else "merged_for_translation.csv"

# Load existing Ukrainian translations, keyed by "Namespace::Key"
existing_uk = {}
with open(csv_path, encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    for row in reader:
        key = row.get('key', '').strip()
        uk = row.get('source_uk', '').strip()
        if key and uk:
            existing_uk[key] = uk

print(f"Loaded {len(existing_uk)} existing Ukrainian translations")

# Load current game JSON (namespace -> {key: english_text})
with open(json_path, encoding='utf-8-sig') as f:
    data = json.load(f)

matched = 0
missing = 0

with open(output_path, 'w', encoding='utf-8', newline='') as f_out:
    writer = csv.writer(f_out)
    writer.writerow(['full_key', 'namespace', 'key', 'source_en', 'existing_uk', 'needs_translation'])
    for namespace, entries in data.items():
        if not isinstance(entries, dict):
            continue
        for key, en_text in entries.items():
            full_key = f"{namespace}::{key}"
            uk_text = existing_uk.get(full_key, '')
            needs = 'NO' if uk_text else 'YES'
            if uk_text:
                matched += 1
            else:
                missing += 1
            writer.writerow([full_key, namespace, key, en_text, uk_text, needs])

print(f"Matched (already translated): {matched}")
print(f"Missing (needs DeepL): {missing}")
print(f"Saved to: {output_path}")