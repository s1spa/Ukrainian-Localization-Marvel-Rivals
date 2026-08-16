import csv
import re
import sys

input_path = sys.argv[1] if len(sys.argv) > 1 else "MarvelRivals_full.csv"
output_path = sys.argv[2] if len(sys.argv) > 2 else "MarvelRivals_english_only.csv"

# Characters that are fine even though not "basic" ASCII (typography, placeholders)
safe_extra = set('’‘“”–—…•°→←×÷™®©€£¥№±%')

def is_mostly_latin(text):
    if not text or not text.strip():
        return False
    total = len(text)
    bad = 0
    for ch in text:
        code = ord(ch)
        if code < 128:  # standard ASCII
            continue
        if ch in safe_extra:
            continue
        if 0x00C0 <= code <= 0x024F:  # Latin extended (accented chars)
            continue
        bad += 1
    # allow up to 5% "weird" characters (icons, stray bytes) before rejecting
    return (bad / total) <= 0.05

kept = 0
skipped = 0

with open(input_path, encoding='utf-8-sig', newline='', errors='replace') as f_in, \
     open(output_path, 'w', encoding='utf-8', newline='') as f_out:
    reader = csv.reader(f_in)
    writer = csv.writer(f_out)
    header = next(reader)
    writer.writerow(header)
    for row in reader:
        if len(row) < 2:
            continue
        source = row[1]
        if is_mostly_latin(source):
            writer.writerow(row)
            kept += 1
        else:
            skipped += 1

print(f"Kept: {kept} rows")
print(f"Skipped: {skipped} rows")
print(f"Saved to: {output_path}")