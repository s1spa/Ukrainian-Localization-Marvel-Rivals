import json
import csv
import sys
from pathlib import Path

# Usage: python 0-mergeloc.py Game.json old_translations.(csv|json) output.csv
# old_translations accepts either:
#   - a CSV with columns "key" ("Namespace::Key") and "source_uk"
#   - a JSON in the same shape as Game_uk_merged_v2.json (namespace -> {key: uk_text})
json_path = sys.argv[1] if len(sys.argv) > 1 else "Game.json"
old_path = sys.argv[2] if len(sys.argv) > 2 else "Game.locres_all.csv"
output_path = sys.argv[3] if len(sys.argv) > 3 else "merged_for_translation.csv"
to_translate_path = sys.argv[4] if len(sys.argv) > 4 else "to_translate.json"

# Load existing Ukrainian translations, keyed by "Namespace::Key"
existing_uk = {}
if Path(old_path).suffix.lower() == '.json':
    with open(old_path, encoding='utf-8-sig') as f:
        old_data = json.load(f)
    for namespace, entries in old_data.items():
        if not isinstance(entries, dict):
            continue
        for key, uk in entries.items():
            uk = (uk or '').strip()
            if uk:
                existing_uk[f"{namespace}::{key}"] = uk
else:
    with open(old_path, encoding='utf-8-sig', newline='') as f:
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
to_translate = {}

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
                to_translate.setdefault(namespace, {})[key] = en_text
            writer.writerow([full_key, namespace, key, en_text, uk_text, needs])

with open(to_translate_path, 'w', encoding='utf-8') as f_out:
    json.dump(to_translate, f_out, ensure_ascii=False, indent=2)

print(f"Matched (already translated): {matched}")
print(f"Missing (needs translation): {missing}")
print(f"Saved to: {output_path}")
print(f"Saved missing-only translation input to: {to_translate_path}")