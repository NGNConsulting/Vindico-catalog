#!/usr/bin/env python3
"""
CI gate for pull requests touching settlements.json.

This does NOT replace the human review the weekly-scan PRs need — it can't
tell whether "Up to $150/household" is the real payout on the official
administrator site, and it isn't meant to. It only catches the mechanical
failure modes a reviewer could plausibly miss when skimming a 20-entry diff:
a REVIEW placeholder left behind, a malformed date, a duplicate id/url, a
field of the wrong type, or a URL scheme that SafeUrl.kt (the Android app's
own launch-time guard) would refuse to open. Every check here is an
unambiguous pass/fail — nothing in this file makes a judgment call about
whether a settlement is real.

Deliberately whole-file, not diff-based: historical entries can legitimately
carry a past deadline (the app hides them at runtime via the same 24h-grace
logic as ClaimDao, it never prunes them from the JSON), so this only flags
malformed dates, not expired ones. Checking the full file each run is also
simpler and catches drift from any prior manual edit, not just this PR's diff.

Stdlib only, matching scrape_candidates.py's convention.

Usage:
    python scripts/validate_catalog.py
Exit code 0 = clean, 1 = one or more issues found (printed to stderr).
"""

import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "settlements.json"

REQUIRED_FIELDS = {
    "id": str,
    "name": str,
    "eligibility": str,
    "payout": str,
    "deadline": str,
    "url": str,
    "keywords": list,
    "noProofRequired": bool,
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Mirrors SafeUrl.kt's allowlist exactly — the catalogue is the trusted
# source that feeds that guard, so it should never itself contain something
# the app would refuse to open.
ALLOWED_SCHEMES = {"http", "https"}


def is_review_placeholder(entry: dict) -> list:
    """Returns which fields still carry the scraper's literal REVIEW marker."""
    hits = []
    if entry.get("payout") == "REVIEW":
        hits.append("payout")
    if entry.get("deadline") == "REVIEW":
        hits.append("deadline")
    eligibility = entry.get("eligibility")
    if isinstance(eligibility, str) and eligibility.startswith("REVIEW:"):
        hits.append("eligibility")
    return hits


def is_safe_url(raw: str) -> bool:
    """Mirrors SafeUrl.kt's toWebUri exactly, including its schemeless
    fallback: 'classactionsettlement.com/file' has no scheme, but the app
    re-parses it as https and accepts it rather than rejecting it — a real,
    common shape from scraped listings, not something to flag here."""
    if not raw or any(c.isspace() or ord(c) < 0x20 for c in raw):
        return False
    parsed = urlparse(raw)
    if parsed.scheme:
        return parsed.scheme.lower() in ALLOWED_SCHEMES and bool(parsed.netloc)
    # No scheme at all — SafeUrl.kt re-parses as "https://" + raw.
    return bool(urlparse(f"https://{raw}").netloc)


def validate_entry(entry, index: int) -> list:
    errors = []
    label = entry.get("id") or entry.get("name") or f"entries[{index}]"

    if not isinstance(entry, dict):
        return [f"{label}: entry is not an object"]

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in entry:
            errors.append(f"{label}: missing required field '{field}'")
            continue
        value = entry[field]
        if not isinstance(value, expected_type):
            errors.append(
                f"{label}: '{field}' should be {expected_type.__name__}, "
                f"got {type(value).__name__}"
            )

    review_fields = is_review_placeholder(entry)
    if review_fields:
        errors.append(
            f"{label}: unresolved REVIEW placeholder in {', '.join(review_fields)} "
            "— verify against the official administrator site before merging"
        )

    deadline = entry.get("deadline")
    if isinstance(deadline, str) and deadline != "REVIEW":
        if not DATE_RE.match(deadline):
            errors.append(f"{label}: 'deadline' is not yyyy-mm-dd: {deadline!r}")
        else:
            try:
                date.fromisoformat(deadline)
            except ValueError:
                errors.append(f"{label}: 'deadline' is not a real calendar date: {deadline!r}")

    url = entry.get("url")
    if isinstance(url, str) and url and not is_safe_url(url):
        errors.append(
            f"{label}: 'url' would be rejected by SafeUrl.kt at launch time: {url!r}"
        )

    keywords = entry.get("keywords")
    if isinstance(keywords, list):
        for kw in keywords:
            if not isinstance(kw, str):
                errors.append(f"{label}: keyword {kw!r} is not a string")

    return errors


def main() -> int:
    if not CATALOG.exists():
        print(f"ERROR: {CATALOG} not found", file=sys.stderr)
        return 1

    try:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: {CATALOG.name} is not valid JSON: {e}", file=sys.stderr)
        return 1

    errors = []

    if not isinstance(catalog.get("settlements"), list):
        print("ERROR: top-level 'settlements' is missing or not a list", file=sys.stderr)
        return 1

    settlements = catalog["settlements"]
    seen_ids, seen_urls = {}, {}

    for i, entry in enumerate(settlements):
        errors += validate_entry(entry, i)

        eid = entry.get("id") if isinstance(entry, dict) else None
        if eid:
            if eid in seen_ids:
                errors.append(f"duplicate id '{eid}' (entries {seen_ids[eid]} and {i})")
            else:
                seen_ids[eid] = i

        eurl = entry.get("url") if isinstance(entry, dict) else None
        if eurl:
            if eurl in seen_urls:
                errors.append(f"duplicate url '{eurl}' (entries {seen_urls[eurl]} and {i})")
            else:
                seen_urls[eurl] = i

    if errors:
        print(f"{len(errors)} issue(s) found in {CATALOG.name}:\n", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"{CATALOG.name}: {len(settlements)} entries, all clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
