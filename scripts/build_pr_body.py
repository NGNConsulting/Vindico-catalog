#!/usr/bin/env python3
"""
Builds the weekly scan PR body, printed to stdout.

Static checklist plus, if fill_drafts.py produced a draft summary this run,
a section naming exactly which entries got auto-drafted, which fields, and
from which source page — so the reviewer's cursory pass is pointed at what
actually changed instead of a blind re-check of everything. Reads from
RUNNER_TEMP (same one-run-artifact location fill_drafts.py writes to, kept
outside the repo checkout on purpose); if it's absent or empty, that section
is simply omitted.
"""

import json
import os
from pathlib import Path

SUMMARY = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "vindico_draft_summary.json"

CHECKLIST = """The weekly scraper found settlements not yet in the catalogue.

Before merging, for each entry still marked REVIEW:
- [ ] Verify it on the **official settlement administrator site**
- [ ] Fill in `eligibility` (one line: who qualifies)
- [ ] Confirm `payout` and `deadline`
- [ ] Prefer the official admin URL over the aggregator link
- [ ] Add `keywords` if receipt-matchable; set `noProofRequired`

For entries marked `"aiDrafted": true`, do a cursory check against the
listed official site rather than a full re-lookup — Claude researched these
on the settlement's own website, but it can misread a page same as a person
can. Check the claim deadline in particular: it is the field that matters
most and the easiest to confuse with the opt-out or hearing date.

Merging deploys to GitHub Pages; installs sync on next launch.
"""


def draft_section() -> str:
    if not SUMMARY.exists():
        return ""
    try:
        items = json.loads(SUMMARY.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    if not items:
        return ""

    drafted = [i for i in items if i.get("fields")]
    flagged = [i for i in items if i.get("flag")]
    out = []

    if drafted:
        lines = ["\n---\n\n### Auto-drafted this run — quick check, not a full re-lookup\n"]
        for item in drafted:
            fields = ", ".join(item.get("fields", []))
            note = f"  \n  note: {item['flag']}" if item.get("flag") else ""
            lines.append(
                f"- **{item.get('name', '?')}** — {fields}  \n  official site: {item.get('source', '?')}{note}"
            )
        out.append("\n".join(lines) + "\n")

    not_drafted = [i for i in flagged if not i.get("fields")]
    if not_drafted:
        lines = ["\n---\n\n### Needs a decision — Claude could not safely fill these\n"]
        for item in not_drafted:
            lines.append(f"- **{item.get('name', '?')}** — {item['flag']}")
        out.append("\n".join(lines) + "\n")

    return "".join(out)


def main() -> None:
    print(CHECKLIST + draft_section())


if __name__ == "__main__":
    main()
