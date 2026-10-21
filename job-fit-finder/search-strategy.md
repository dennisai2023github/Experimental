# Search Strategy (US / UK / Canada)

Used by Step 1 of SKILL.md. Queries are built from `profile.md`; edit this file to tune coverage.

## Principles
- **Search by function, not title.** The same work is posted under different titles. Each priority tier lists several plausible titles as variants.
- **Prefer primary sources.** Employer career pages and ATS links beat aggregator pages: `boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`, `*.myworkdayjobs.com`, `careers.<company>.com`.
- **Recency.** Add "posted last 14 days" intent: include the current month/year in queries and skip anything clearly older.
- **Store a URL that resolves to the posting itself**, not a search-results or listing page.

## Priority tiers (fill from profile.md)
1. **Primary role function** - your most desired direction, 3-4 title variants.
2. **Domain expertise** - roles where your domain knowledge is the edge.
3. **Adjacent roles** - pivots you could credibly make.
4. **Broader / stretch** (only on `broad`).

Default run = tiers 1-3, up to 3 queries each.

## Query templates

Replace `<T>` with a title variant, `<L>` with city/region or "remote", `<K>` with a key skill.

General:
```
"<T>" "<L>" jobs posted this month
"<T>" "<K>" "<L>" apply
```
ATS-targeted:
```
site:boards.greenhouse.io "<T>" "<L>"
site:jobs.lever.co "<T>" "<L>"
site:jobs.ashbyhq.com "<T>"
site:myworkdayjobs.com "<T>" "<L>"
```
Aggregators (fallback, then follow through to the employer link):
```
site:linkedin.com/jobs/view "<T>" "<L>"
site:indeed.com/viewjob "<T>" "<L>"
site:glassdoor.com/job-listing "<T>" "<L>"
```
Country notes:
- **US**: include state or "remote US"; watch for "authorized to work without sponsorship" wording (Eligibility Gate).
- **UK**: Reed, Totaljobs, CWJobs, Civil Service Jobs, gov.uk "Find a job"; watch "right to work in the UK", "SC/DV clearance".
- **Canada**: Job Bank (jobbank.gc.ca), Workopolis, Indeed.ca; watch "legally entitled to work in Canada", "Reliability/Secret clearance".

Language scope: write each tier in every language listed in your Languages table.

## Tuning
After a run, if results are mostly Weak/Poor, tighten tier 1 titles or add a domain keyword; if too few results, run `broad` or loosen location to "remote".
