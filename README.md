# Vindico Settlements Catalogue

`settlements.json` is the remote catalogue the app syncs on every launch and
nightly. Editing this file (once hosted) pushes new settlements to all
installs — no app update needed.

## Hosting (one-time setup, free)

1. Create a public GitHub repo, e.g. `vindico-catalog`.
2. Put `settlements.json` at the repo root and enable GitHub Pages
   (Settings → Pages → Deploy from branch → main).
3. Your catalogue URL becomes:
   `https://<your-username>.github.io/vindico-catalog/settlements.json`
4. Update `REMOTE_URL` in
   `app/src/main/java/com/vindico/app/data/catalog/SettlementCatalog.kt`
   to that URL and ship the release.

Until the URL is live the app runs on its built-in seed list — the sync
fails silently by design.

## Adding a settlement

```json
{
  "id": "unique-slug-2026",              // stable — never reuse or rename
  "name": "Brand Thing Settlement",
  "eligibility": "One line: who qualifies.",
  "payout": "Up to $50/household",
  "deadline": "2026-12-31",              // ISO date; entry auto-hides after
  "url": "https://official-settlement-site.com",
  "keywords": ["brand", "product name"], // receipt-match terms; [] = browse-only
  "noProofRequired": true                // shows the NO PROOF NEEDED badge
}
```

## Weekly automation (Option A: auto-draft, human merge)

`.github/workflows/update-catalog.yml` runs every Monday (or on demand via
the Actions tab → "Run workflow"). It executes
`scripts/scrape_candidates.py`, which:

1. Pulls the open-settlements feed from Top Class Actions and the listing
   from Class Action Buddy (facts only — names, dates, amounts, links).
2. Skips anything already in the catalogue (fuzzy name match), anything
   past deadline, and anything previously proposed (`scripts/seen.json`
   is the rejection ledger — deleting a candidate from a PR is permanent).
3. Adds up to 20 new candidates per run as **draft entries** with
   `REVIEW` placeholders and opens a pull request.

Your job per PR (~2 minutes per entry): verify on the official
administrator site, fill in eligibility/payout/deadline/keywords, swap in
the official URL, delete anything irrelevant, merge. Merge → Pages
redeploys → every install syncs on next launch.

If both sources fail to scrape (site redesign, bot-blocking), the workflow
fails loudly and GitHub emails you — it never silently stops proposing.

Test locally anytime: `python scripts/scrape_candidates.py --dry-run`

## Rules (editorial content — this is the product's trust surface)

- **Every entry must be a real, verified, currently open settlement.**
  Check the official administrator site before adding.
- Prefer the official settlement administrator URL. An aggregator page is
  acceptable only if it links to the official site.
- Never invent payout numbers — use the settlement's own language.
- Entries past their deadline disappear automatically; you don't need to
  remove them (but pruning keeps the file tidy).
- Existing installs keep the last synced copy when offline.

Seed list last verified: 2026-07-10.
