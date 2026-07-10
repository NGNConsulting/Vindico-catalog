#!/usr/bin/env python3
"""
Weekly candidate scraper for the Vindico settlements catalogue.

Fetches open-settlement listings from aggregators (Top Class Actions RSS,
Class Action Buddy), extracts FACTS ONLY (name, deadline, payout, link —
never their editorial descriptions), and appends any settlement not already
in settlements.json as a draft entry marked for human review.

Draft entries are NOT user-ready: eligibility contains a REVIEW placeholder
and keywords is empty. The reviewer verifies each entry against the official
settlement administrator site before merging the PR — the whole point of the
pipeline is that a human gate stays in place.

scripts/seen.json records every URL ever proposed, so a candidate you reject
(delete from the PR) is never proposed again.

Stdlib only — no pip installs to break on a runner.

Usage:
    python scripts/scrape_candidates.py              # patches settlements.json
    python scripts/scrape_candidates.py --dry-run    # print only, no writes
    python scripts/scrape_candidates.py --limit 50   # override per-run cap
"""

import html
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

ROOT    = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "settlements.json"
SEEN    = ROOT / "scripts" / "seen.json"

TCA_FEED = "https://topclassactions.com/category/lawsuit-settlements/open-lawsuit-settlements/feed/"
CAB_PAGE = "https://classactionbuddy.com/blog/class-action-settlements-no-proof-2026/"

UA = "VindicoCatalogBot/1.0 (settlement catalogue curation; contact via repo)"

# Keep PRs reviewable; the backlog drains over successive weekly runs.
DEFAULT_LIMIT = 20

MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}
MONTHS.update({m[:3].lower(): i for m, i in list(MONTHS.items())})


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def clean(text: str) -> str:
    """Unescape entities, normalize unicode, drop OCR/encoding junk."""
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text).replace("�", "")
    text = re.sub(r"^[\W_]+", "", text)          # leading "› ", quotes, dashes
    return re.sub(r"\s+", " ", text).strip()


def parse_date(text: str):
    """Best-effort date extraction → ISO yyyy-mm-dd, or None."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(?i)([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if m:
        mo = MONTHS.get(m.group(1).lower())
        if mo:
            try:
                return date(int(m.group(3)), mo, int(m.group(2))).isoformat()
            except ValueError:
                return None
    return None


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"{slug[:48].rstrip('-')}-{date.today().year}"


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return 1.0 - levenshtein(a, b) / max(len(a), len(b))


def already_known(name: str, existing_norms: list) -> bool:
    """Fuzzy dedupe: 'Cosequin dog supplements' must match
    'Cosequin Dog Joint Supplement' even though the phrasing differs."""
    n = norm(name)
    return any(similar(n, e) >= 0.65 or n in e or e in n for e in existing_norms)


# ── Source: Top Class Actions (RSS + best-effort article facts) ───────────────

def tca_candidates():
    out = []
    root = ElementTree.fromstring(fetch(TCA_FEED))
    for item in root.iter("item"):
        title = clean(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        if not title or not link or "settlement" not in title.lower():
            continue
        name = re.sub(r"(?i)\s*class action settlement.*$", "", title).strip()
        # Drop leading fund size — "$" may already be stripped by clean(),
        # so it's optional here: "$1.9M Foo" and "1.9M Foo" both → "Foo".
        name = re.sub(r"(?i)^\$?[\d.,]+\s*[MBK]?\s+", "", name)
        deadline, payout = None, None
        try:
            # Article pages often bot-block (403) — facts become REVIEW then.
            article = fetch(link)
            box = re.search(r"(?is)claim\s*form\s*deadline\s*[:<].{0,120}?([A-Za-z0-9/,. ]{6,40})", article)
            if box:
                deadline = parse_date(box.group(1))
            # Window capped tight so sidebar/trending widgets can't bleed in.
            award = re.search(r"(?is)potential\s*award\s*[:<].{0,160}?(\$[\d,]+(?:\.\d{2})?)", article)
            if award:
                payout = award.group(1)
        except Exception:
            pass
        out.append({"name": name, "url": link, "deadline": deadline, "payout": payout})
    return out


# ── Source: Class Action Buddy (HTML listing) ────────────────────────────────

def cab_candidates():
    out = []
    page = fetch(CAB_PAGE)
    for m in re.finditer(
        r'(?is)<a[^>]+href="(/settlements/[^"]+)"[^>]*>([^<]{6,90})</a>(.{0,300}?)(?=<a[^>]+href="/settlements/|$)',
        page,
    ):
        path, raw_name, tail = m.group(1), clean(m.group(2)), m.group(3)
        if "settlement" not in raw_name.lower():
            continue
        name = re.sub(r"(?i)\s*settlement.*$", "", raw_name).strip()
        if len(name) < 6:
            continue
        payout_m = re.search(r"\$[\d,]+(?:\.\d{2})?(?:\s*/\s*(?:household|person|claim))?", tail)
        out.append({
            "name": name,
            "url": f"https://classactionbuddy.com{path}",
            "deadline": parse_date(tail),
            "payout": payout_m.group(0) if payout_m else None,
        })
    return out


# ── Merge into catalogue ──────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    limit = DEFAULT_LIMIT
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    seen = set(json.loads(SEEN.read_text(encoding="utf-8"))) if SEEN.exists() else set()
    existing_norms = [norm(s["name"]) for s in catalog["settlements"]]
    existing_urls = {s.get("url") or "" for s in catalog["settlements"]}

    candidates, failures = [], 0
    for label, source in (("Top Class Actions", tca_candidates),
                          ("Class Action Buddy", cab_candidates)):
        try:
            found = source()
            print(f"{label}: {len(found)} settlements listed")
            candidates += found
        except Exception as e:
            failures += 1
            print(f"ERROR: {label} scrape failed: {e}", file=sys.stderr)

    if failures == 2:
        print("ERROR: all sources failed — investigate scraper/site changes", file=sys.stderr)
        sys.exit(1)

    today = date.today().isoformat()
    fresh, skipped_backlog = [], 0
    for c in candidates:
        if not c["name"] or c["url"] in seen or c["url"] in existing_urls:
            continue
        if already_known(c["name"], existing_norms):
            seen.add(c["url"])       # known settlement, alternate phrasing
            continue
        if c["deadline"] and c["deadline"] < today:
            seen.add(c["url"])       # already closed
            continue
        if len(fresh) >= limit:
            skipped_backlog += 1     # NOT marked seen — proposed next run
            continue
        existing_norms.append(norm(c["name"]))
        seen.add(c["url"])
        fresh.append({
            "id": slugify(c["name"]),
            "name": c["name"],
            "eligibility": "REVIEW: verify on official administrator site and describe who qualifies.",
            "payout": c["payout"] or "REVIEW",
            "deadline": c["deadline"] or "REVIEW",
            "url": c["url"],
            "keywords": [],
            "noProofRequired": False,
        })

    if not fresh:
        print("No new candidates — catalogue is up to date.")
        return

    print(f"\n{len(fresh)} new candidate(s)"
          + (f" ({skipped_backlog} more queued for next run)" if skipped_backlog else "") + ":")
    for f in fresh:
        print(f"  - {f['name']} (deadline {f['deadline']}, payout {f['payout']})")

    if dry_run:
        return

    catalog["settlements"].extend(fresh)
    catalog["updated"] = today
    CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    SEEN.write_text(json.dumps(sorted(seen), indent=2) + "\n", encoding="utf-8")
    print(f"\nPatched {CATALOG.name} and seen.json. Review the REVIEW placeholders before merging!")


if __name__ == "__main__":
    main()
