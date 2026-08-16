import csv
import json
import sys

csv_path = sys.argv[1] if len(sys.argv) > 1 else "Game.csv"
json_path = sys.argv[2] if len(sys.argv) > 2 else "Game_uk_merged.json"
output_path = sys.argv[3] if len(sys.argv) > 3 else "Game_translated.csv"

with open(json_path, encoding='utf-8-sig') as f:
    data = json.load(f)

lookup = {}
for namespace, entries in data.items():
    if not isinstance(entries, dict):
        continue
    for key, text in entries.items():
        lookup[f"{namespace}/{key}"] = text  # <-- the correct separator

matched = 0
missing = 0
missing_keys = []

with open(csv_path, encoding='utf-8-sig', newline='') as f_in:
    reader = csv.reader(f_in)
    header = next(reader)
    rows = list(reader)

print(f"CSV header: {header}")
print(f"Total rows: {len(rows)}")

with open(output_path, 'w', encoding='utf-8', newline='') as f_out:
    writer = csv.writer(f_out)
    writer.writerow(header)
    for row in rows:
        if len(row) < 1:
            writer.writerow(row)
            continue
        full_key = row[0]
        found = lookup.get(full_key)
        if found:
            matched += 1
            new_row = row[:]
            if len(new_row) >= 3:
                new_row[2] = found
            else:
                new_row.append(found)
            writer.writerow(new_row)
        else:
            missing += 1
            missing_keys.append(full_key)
            writer.writerow(row)

print(f"Matched: {matched}")
print(f"Missing: {missing}")
if missing_keys[:5]:
    print("Sample missing keys:", missing_keys[:5])
print(f"Saved to: {output_path}")