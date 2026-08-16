"""
Marvel Rivals UA Translation Script
Translates to_translate.json (namespace -> {key: en_text}) to Ukrainian via DeepL,
merges with uk_existing.json (already-translated strings from the old KUBIK mod),
and writes the final merged JSON ready for CSV/.locres conversion.
"""
import os
import sys
import json
import re
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import deepl
from deep_translator import GoogleTranslator
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

load_dotenv()

BASE_DIR = Path.cwd()  # run this from the repo root (where Game.json etc. live), not from scripts/
TO_TRANSLATE_PATH = BASE_DIR / "to_translate.json"
EXISTING_UK_PATH = BASE_DIR / "uk_existing.json"
OUTPUT_PATH = BASE_DIR / "translated_uk.json"          # newly translated only (resumable cache)
MERGED_OUTPUT_PATH = BASE_DIR / "Game_uk_merged.json"  # existing + newly translated, final result

BATCH_SIZE = 300      # Gemini is the primary translator now — free tier caps REQUESTS/day (not chars),
MAX_BATCH_CHARS = 40_000  # so fewer, bigger batches matter more than small "clean" ones here
WORKERS = 3

DEEPL_API_KEYS = os.environ.get("DEEPL_API_KEYS", "").split(",")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# "gemini-flash-latest" resolved to gemini-3.7-flash, whose free tier is capped at 20 requests/DAY —
# unusable for bulk batches. flash-lite tiers have a much higher free RPD budget.
GEMINI_MODEL = "gemini-flash-lite-latest"
_gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
_GEMINI_RESPONSE_SCHEMA = genai_types.Schema(
    type=genai_types.Type.ARRAY,
    items=genai_types.Schema(
        type=genai_types.Type.OBJECT,
        properties={
            "id": genai_types.Schema(type=genai_types.Type.INTEGER),
            "translation": genai_types.Schema(type=genai_types.Type.STRING),
        },
        required=["id", "translation"],
    ),
)
_GEMINI_SYSTEM_PROMPT = """You are translating video game UI/lore text from English to Ukrainian for the game Marvel Rivals.
Rules:
- Translate naturally, fitting the tone of a superhero action game.
- NEVER translate or modify text inside curly braces like {PlayerName} or {key=Value} - copy them verbatim, placed naturally for Ukrainian grammar.
- NEVER translate or modify XML-like tags such as <Debuff>, <R>, </>, <keywidget .../> - copy them verbatim, INCLUDING every attribute (e.g. <imgtext id="Icon_X"></> must keep id="Icon_X" exactly), and keep them wrapping the same word/phrase they wrapped in the English source, even when the tag pair is empty like <imgtext id="Icon_X"></>.
- Return ONLY a JSON array of objects with fields id (int, matching input id) and translation (string), one per input item, same count as input.
"""

# Marvel Rivals uses Unreal's richtext markup, not Minecraft's:
#   {PlayerName}, {key=LeftControl}                -> curly-brace placeholders
#   <Debuff>...</>, <Y>4</>                          -> UE richtext tags with SHORTHAND closing tag </>
#   <keywidget id="KeyWidget" key="{KeyText}"/>      -> attributed self-closing tags, can run long
#
# For DeepL we don't just mask these — a wrapping tag like <Debuff>Taunt</> must reach DeepL as a
# REAL paired XML tag (<Debuff>Taunt</Debuff>), or DeepL is free to separate the tag from the word it
# wraps when it reorders the sentence (confirmed: "<Y>4</>" -> content and tag drifted apart, and one
# case duplicated the wrapped word). Stack-based pairing below expands "</>" to match its opening tag,
# translates, then collapses "</TagName>" back to the shorthand "</>" the game expects.
TAG_RE = re.compile(
    r'</>'
    r'|<([a-zA-Z][\w]*)([^>]{0,200}?)(/)?>'   # group1=tagname, group3='/' if self-closing
    r'|\{[^{}]{1,80}\}'                        # {PlaceholderName} / {key=Value}
    r'|\\n|\\t'
)
# Simpler pattern used only for the Google fallback path (no XML tag awareness there — everything
# opaque is safest) and for the needs-translation check.
PLACEHOLDER_PATTERN = re.compile(
    r'</>'
    r'|<[a-zA-Z][^>]{0,200}/?>'
    r'|\{[^{}]{1,80}\}'
    r'|\\n|\\t'
)


def _escape(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def protect(text: str) -> tuple[str, list[str]]:
    """DeepL-bound protection: pairs up <Tag>...</> into real <Tag>...</Tag> XML so DeepL keeps
    the tag attached to its content; everything else (placeholders, self-closing tags, orphan/
    unmatched tags) becomes an opaque <x id="n"/> token DeepL leaves untouched."""
    matches = list(TAG_RE.finditer(text))
    decisions: list = [None] * len(matches)
    stack: list[int] = []
    for i, m in enumerate(matches):
        tagname = m.group(1)
        selfclose = m.group(3)
        if tagname is not None and not selfclose:
            stack.append(i)
            decisions[i] = 'pending'
        elif m.group(0) == '</>':
            if stack:
                open_idx = stack.pop()
                otag = matches[open_idx].group(1)
                decisions[open_idx] = ('open_paired', otag, matches[open_idx].group(0))
                decisions[i] = ('close_paired', otag)
            else:
                decisions[i] = 'opaque'
        else:
            decisions[i] = 'opaque'
    for idx in stack:
        decisions[idx] = 'opaque'  # opening tag never closed in this string — treat as opaque

    tokens: list[str] = []
    out: list[str] = []
    last_end = 0
    for i, m in enumerate(matches):
        out.append(_escape(text[last_end:m.start()]))
        last_end = m.end()
        d = decisions[i]
        if isinstance(d, tuple) and d[0] == 'open_paired':
            out.append(d[2])  # emit the ORIGINAL opening tag verbatim — keeps attrs like id="..."
        elif isinstance(d, tuple) and d[0] == 'close_paired':
            out.append(f'</{d[1]}>')
        else:
            idx = len(tokens)
            tokens.append(m.group(0))
            out.append(f'<x id="{idx}"/>')
    out.append(_escape(text[last_end:]))
    return ''.join(out), tokens


def restore(text: str, tokens: list[str]) -> str:
    def _sub(m):
        idx = int(m.group(1))
        return tokens[idx] if idx < len(tokens) else m.group(0)
    text = re.sub(r'<x\s+id="(\d+)"\s*/>', _sub, text)
    text = re.sub(r'</[a-zA-Z]\w*>', '</>', text)  # collapse expanded closing tags back to UE shorthand
    return text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')


_GOOGLE_PLACEHOLDER = re.compile(r'⌊(\d+)⌋')


def protect_for_google(text: str) -> tuple[str, list[str]]:
    tokens = []
    def _replace(m):
        idx = len(tokens)
        tokens.append(m.group(0))
        return f"⌊{idx}⌋"
    return PLACEHOLDER_PATTERN.sub(_replace, text), tokens


def restore_from_google(text: str, tokens: list[str]) -> str:
    return _GOOGLE_PLACEHOLDER.sub(lambda m: tokens[int(m.group(1))] if int(m.group(1)) < len(tokens) else m.group(0), text)


def translate_batch_gemini(batch_keys, original_texts, original_map, _depth=0):
    """Gemini sees the raw English (tags and all) and is instructed to preserve markup itself —
    no protect/restore needed, it handles Unreal richtext tags and {placeholders} natively."""
    items = [{"id": i, "text": t} for i, t in enumerate(original_texts)]
    prompt = _GEMINI_SYSTEM_PROMPT + "\nInput:\n" + json.dumps(items, ensure_ascii=False)

    rate_limit_retries = 0
    attempt = 0
    while True:
        try:
            resp = _gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_GEMINI_RESPONSE_SCHEMA,
                ),
            )
            results = json.loads(resp.text)
            if len(results) != len(batch_keys):
                raise ValueError(f"Expected {len(batch_keys)} results, got {len(results)}")
            by_id = {r["id"]: r["translation"] for r in results}
            out = {}
            for i, k in enumerate(batch_keys):
                out[k] = by_id[i] if i in by_id and by_id[i].strip() else original_map[k]
            return out
        except Exception as e:
            is_rate_limit = "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e)
            if is_rate_limit and rate_limit_retries < 8:
                # a 429 means "too many requests" — splitting the batch only makes MORE requests,
                # which makes it worse. Just wait it out and retry the same batch.
                m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+)", str(e))
                wait = int(m.group(1)) + 2 if m else 30 * (rate_limit_retries + 1)
                print(f"\n  [RATE LIMIT] Gemini 429 — waiting {wait}s...")
                time.sleep(wait)
                rate_limit_retries += 1
                continue
            attempt += 1
            if attempt <= 2:
                time.sleep(2 * attempt)
                continue
            if len(batch_keys) > 1 and _depth < 6:
                mid = len(batch_keys) // 2
                out = {}
                out.update(translate_batch_gemini(batch_keys[:mid], original_texts[:mid], original_map, _depth + 1))
                out.update(translate_batch_gemini(batch_keys[mid:], original_texts[mid:], original_map, _depth + 1))
                return out
            print(f"\n  [WARN] Gemini gave up on 1 string, trying Google: {e}")
            return translate_batch_google(batch_keys, original_texts, original_map)


def translate_batch_google(batch_keys, original_texts, original_map, _depth=0):
    protected, token_lists = [], []
    for text in original_texts:
        p, tokens = protect_for_google(text)
        protected.append(p)
        token_lists.append(tokens)

    for attempt in range(3):
        try:
            results = GoogleTranslator(source='en', target='uk').translate_batch(protected)
            out = {}
            for k, r, tokens in zip(batch_keys, results, token_lists):
                out[k] = restore_from_google(r, tokens) if r else original_map[k]
            return out
        except Exception as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            # one bad string can poison a whole batch — split and isolate it instead of
            # giving up on everyone; bottoms out at single-item translation.
            if len(batch_keys) > 1 and _depth < 6:
                mid = len(batch_keys) // 2
                out = {}
                out.update(translate_batch_google(batch_keys[:mid], original_texts[:mid], original_map, _depth + 1))
                out.update(translate_batch_google(batch_keys[mid:], original_texts[mid:], original_map, _depth + 1))
                return out
            print(f"\n  [WARN] Google fallback gave up on 1 string: {e}")
            return {k: original_map[k] for k in batch_keys}


def _needs_translation(val: str) -> bool:
    if not isinstance(val, str) or not val.strip():
        return False
    content_only = PLACEHOLDER_PATTERN.sub('', val).strip()
    return bool(re.search(r'[a-zA-Z]', val)) and len(content_only) >= 2


class KeyPool:
    """Thread-safe pool of DeepL translators — switches key on quota exhaustion."""

    def __init__(self, keys: list[str]):
        self._translators = [deepl.Translator(k) for k in keys]
        self._idx = 0
        self._lock = threading.Lock()

    def get(self) -> deepl.Translator | None:
        with self._lock:
            if self._idx < len(self._translators):
                return self._translators[self._idx]
            return None

    def next_key(self) -> bool:
        with self._lock:
            self._idx += 1
            if self._idx < len(self._translators):
                print(f"\n  [KEY SWITCH] Switching to key #{self._idx + 1}")
                return True
            return False

    def print_usage(self):
        for i, t in enumerate(self._translators):
            try:
                u = t.get_usage()
                print(f"  Key #{i + 1}: {u.character.count:,} / {u.character.limit:,} chars")
            except Exception as e:
                print(f"  Key #{i + 1}: unavailable ({e})")


def translate_batch(batch_keys, batch_texts, fallback, original_texts, original_map, pool, failed_keys):
    for attempt in range(6):
        translator = pool.get()
        if translator is None:
            return translate_batch_gemini(batch_keys, original_texts, original_map)
        try:
            results = translator.translate_text(
                batch_texts,
                source_lang="EN",
                target_lang="UK",
                tag_handling="xml",
                ignore_tags=["x"],
                preserve_formatting=True,
            )
            if len(results) != len(batch_keys):
                raise ValueError(f"Expected {len(batch_keys)} results, got {len(results)}")
            return {k: (r.text or fallback[k]) for k, r in zip(batch_keys, results)}
        except deepl.TooManyRequestsException:
            wait = 2 ** min(attempt, 4)
            print(f"\n  [RATE LIMIT] waiting {wait}s...")
            time.sleep(wait)
        except deepl.QuotaExceededException:
            print(f"\n  [QUOTA] Key #{pool._idx + 1} exhausted, switching...")
            if not pool.next_key():
                return translate_batch_gemini(batch_keys, original_texts, original_map)
        except Exception as e:
            print(f"\n  [WARN] DeepL batch error ({len(batch_keys)} items): {e} — falling back to Gemini")
            return translate_batch_gemini(batch_keys, original_texts, original_map)
    print(f"\n  [WARN] DeepL batch failed after retries — falling back to Gemini")
    return translate_batch_gemini(batch_keys, original_texts, original_map)


def load_json(path: Path) -> dict:
    if path.exists():
        with open(path, encoding='utf-8-sig') as f:
            return json.load(f)
    return {}


def save_json(path: Path, data: dict):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    to_translate_src = load_json(TO_TRANSLATE_PATH)
    existing_uk = load_json(EXISTING_UK_PATH)
    already_translated = load_json(OUTPUT_PATH)  # resume support across re-runs

    key_pool = KeyPool(DEEPL_API_KEYS)
    if DEEPL_API_KEYS:
        print("DeepL usage:")
        key_pool.print_usage()
        print()
    else:
        print("[WARN] No DeepL keys configured — everything will go through Google Translate fallback\n")

    # skip_results collects everything that doesn't need a DeepL call this run:
    # strings resumed from a previous run, and strings that are placeholder-only / empty.
    skip_results: dict[str, dict] = {ns: dict(already_translated.get(ns, {})) for ns in to_translate_src}
    protected_map: dict[str, tuple[str, list]] = {}
    fallback_map: dict[str, str] = {}
    original_map: dict[str, str] = {}

    for ns, entries in to_translate_src.items():
        for key, en_text in entries.items():
            if key in skip_results.get(ns, {}):
                continue
            if not _needs_translation(en_text):
                skip_results.setdefault(ns, {})[key] = en_text
                continue
            p, tokens = protect(en_text)
            ck = f"{ns}\x00{key}"
            protected_map[ck] = (p, tokens)
            fallback_map[ck] = p
            original_map[ck] = en_text

    to_translate = list(protected_map.keys())
    total = len(to_translate)
    resumed = sum(len(v) for v in already_translated.values())
    print(f"Resumed from cache: {resumed:,} | To translate this run: {total:,}\n")

    if to_translate:
        batches = []
        cur_keys, cur_texts, cur_chars = [], [], 0
        for ck in to_translate:
            text = protected_map[ck][0]
            tlen = len(text)
            if cur_keys and (len(cur_keys) >= BATCH_SIZE or cur_chars + tlen > MAX_BATCH_CHARS):
                batches.append((cur_keys, cur_texts))
                cur_keys, cur_texts, cur_chars = [], [], 0
            cur_keys.append(ck)
            cur_texts.append(text)
            cur_chars += tlen
        if cur_keys:
            batches.append((cur_keys, cur_texts))
        print(f"Translating {total:,} strings — {len(batches)} batches, {WORKERS} workers")
        print(f"Progress: 0 / {total:,}", end='', flush=True)

        results_flat: dict[str, str] = {}
        failed_keys: set[str] = set()
        done = 0
        lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(
                    translate_batch,
                    bkeys, btexts,
                    fallback_map,
                    [original_map[ck] for ck in bkeys],
                    original_map,
                    key_pool,
                    failed_keys,
                ): bkeys
                for bkeys, btexts in batches
            }
            for future in as_completed(futures):
                translated = future.result()
                with lock:
                    results_flat.update(translated)
                    done += len(translated)
                    print(f"\r  Progress: {done:,} / {total:,}   ", end='', flush=True)

        print(f"\r  Progress: {total:,} / {total:,} done          ")

        if failed_keys:
            print(f"[WARN] {len(failed_keys):,} keys failed (quota/errors) — left untranslated, re-run script to retry them")

        for ck, tr in results_flat.items():
            ns, key = ck.split("\x00", 1)
            _, tokens = protected_map[ck]
            skip_results.setdefault(ns, {})[key] = restore(tr, tokens)
    else:
        print("Nothing new to translate.")

    save_json(OUTPUT_PATH, skip_results)
    print(f"Saved: {OUTPUT_PATH}")

    # Final merge: human-curated existing_uk wins over machine translation on key collisions
    merged: dict[str, dict] = {}
    for ns in set(existing_uk) | set(skip_results):
        merged[ns] = {**skip_results.get(ns, {}), **existing_uk.get(ns, {})}
    save_json(MERGED_OUTPUT_PATH, merged)
    print(f"Saved merged: {MERGED_OUTPUT_PATH}")


if __name__ == "__main__":
    # Usage: python 0-translate_rivals.py [to_translate.json] [Game_uk_merged_v2.json] [output.json]
    # All arguments are optional - omit any of them to keep the default path set above.
    if len(sys.argv) > 1:
        TO_TRANSLATE_PATH = Path(sys.argv[1])
    if len(sys.argv) > 2:
        EXISTING_UK_PATH = Path(sys.argv[2])
    if len(sys.argv) > 3:
        MERGED_OUTPUT_PATH = Path(sys.argv[3])
    main()
