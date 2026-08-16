import json
import csv
import sys

# Usage: python 0-build_csv.py [Game.json] [Game_uk_merged_v2.json] [output.csv]
# Builds the CSV UnrealLocres.exe import expects (key,source,target - key formatted as "namespace/key")
# from the translation JSON, pulling the English source text from Game.json for reference.
en_path = sys.argv[1] if len(sys.argv) > 1 else "Game.json"
uk_path = sys.argv[2] if len(sys.argv) > 2 else "Game_uk_merged_v2.json"
output_path = sys.argv[3] if len(sys.argv) > 3 else "Game_full_translated.csv"

with open(uk_path, encoding='utf-8') as f:
    uk_data = json.load(f)
with open(en_path, encoding='utf-8-sig') as f:
    en_data = json.load(f)

count = 0
with open(output_path, 'w', encoding='utf-8', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['key', 'source', 'target'])
    for namespace, entries in uk_data.items():
        for key, uk_text in entries.items():
            en_text = en_data.get(namespace, {}).get(key, '')
            writer.writerow([f"{namespace}/{key}", en_text, uk_text])
            count += 1

print(f"Wrote {count} rows to {output_path}")
