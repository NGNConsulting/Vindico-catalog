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
   `REVIEW` placeholders.

Then `scripts/fill_drafts.py` (needs the `ANTHROPIC_API_KEY` repo secret; web
search is on by default for the key's organization unless an admin disabled it
under Claude Console → Settings → Capabilities) asks
Claude to find each settlement's **official administrator site** with the
API's server-side web search/fetch tools, and fills in eligibility, payout,
claim deadline and the official URL. Filled entries are stamped
`"aiDrafted": true`. It never overwrites a field a human already resolved,
refuses to write anything unless the official site was actually visited,
and flags — never deletes — a settlement whose site shows no payment or no
claim process, or whose claim window is already closed or closes within 3
days (too soon to survive review, merge and an app sync; the PR body lists
these under "Needs a decision"). Anything it can't settle with high confidence stays `REVIEW`.
(Aggregator article pages can't be fetched from a GitHub runner — they
return HTTP 403 — which is why the drafts come from the official site.)
Finally the workflow opens (or updates) a pull request.

Your job per PR: spot-check the `aiDrafted` entries — **the claim deadline
first** (easy to confuse with the opt-out or hearing date) — do a full
lookup on anything still `REVIEW`, delete anything irrelevant, merge.
Merge → Pages redeploys → every install syncs on next launch.

### Processing the weekly PR — how it actually behaves

- There is **one rolling PR** on branch `catalog/candidates`. It is not a
  new PR each week: every Monday the workflow re-checks-out `main`, rebuilds
  that branch, and force-updates the same PR. **Commits you push to that
  branch are discarded at the next Monday run (13:00 UTC), so review and
  merge it before then.**
- `seen.json` only advances when a PR merges. An unmerged PR therefore
  freezes the queue: the same top 20 candidates are re-proposed each week
  and the rest of the backlog never surfaces.
- The `validate-catalog` check goes red until every `REVIEW` placeholder is
  gone. If its run shows "action required" instead, open the PR's Checks
  tab and click **Approve and run workflows**.
- To reject a candidate, delete its block from the PR diff; its URL is
  already in `seen.json`, so it is never proposed again.
- After changing anything under `scripts/`, run
  `python scripts/test_fill_drafts.py` (offline, no API key needed).

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
