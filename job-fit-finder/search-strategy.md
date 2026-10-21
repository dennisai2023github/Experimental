# Search Strategy (Canada: Remote and Hybrid only)

Used by Step 1 of SKILL.md. Queries are built from `profile.md`; edit this file to tune coverage.

## Scope (hard filter)
- **Remote**: only roles a person living in Canada can hold. Keep "Remote - Canada", "Remote (Canada)", "Remote - North America" and "Remote - Americas" when Canada is not excluded. Drop "Remote - US only", "Remote - UK/EU", or roles tied to a US state or other country.
- **Hybrid**: only roles with a Canadian office/location (any province, or the cities in `profile.md`).
- **Excluded**: fully on-site roles, and any role outside Canada. Do not score them; list them under "Out of scope" with the reason.

## Principles
- **Search by function, not title.** The same work is posted under different titles. Each priority tier lists several plausible titles as variants.
- **Prefer primary sources.** Employer career pages and ATS links beat aggregator pages.
- **Recency.** Include the current month/year in queries; skip anything clearly older than ~30 days.
- **Store a URL that resolves to the posting itself**, not a search-results page.
- Include French variants (e.g. "teletravail", "hybride", "poste a distance") only if French is in your Languages table.

## Public job boards (Canada)
General:
- Job Bank (jobbank.gc.ca) - federal, filter "Work from home" / remote
- Indeed Canada (ca.indeed.com), LinkedIn Jobs (location: Canada; filters Remote / Hybrid)
- Glassdoor Canada, Workopolis, Jobboom, Monster Canada, ZipRecruiter Canada
- Talent.com, Adzuna Canada, Jooble Canada, Neuvoo/Talent
- Government: GC Jobs (Government of Canada), provincial portals (Ontario Public Service, WorkBC, Jobs Alberta, Emploi-Quebec)

Remote-focused:
- We Work Remotely, Remote OK, Remotive, Working Nomads, FlexJobs (partly paywalled; use free listings only), Himalayas, Wellfound (AngelList), Jobgether

Tech / startup:
- Wellfound, Hacker News "Who is hiring", BetaKit Jobs, Ottawa/Toronto/Vancouver/Montreal tech boards, Y Combinator Work at a Startup

Niche (add per profile): charityvillage.com (non-profit), higheredjobs / university career pages (academia), Eluta.ca (indexes employer career pages directly)

ATS pages (primary sources):
`boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`, `*.myworkdayjobs.com`, `careers.<company>.com`, `apply.workable.com`

## Priority tiers (fill from profile.md)
1. **Primary role function** - your most desired direction, 3-4 title variants.
2. **Domain expertise** - roles where your domain knowledge is the edge.
3. **Adjacent roles** - pivots you could credibly make.
4. **Broader / stretch** (only on `broad`).

Default run = tiers 1-3, up to 3 queries each, split across remote and hybrid.

## Query templates
Replace `<T>` with a title variant, `<K>` with a key skill, `<City>` with a Canadian city from the profile.

Remote:
```
"<T>" "remote" "Canada" apply
"<T>" "remote - canada" site:boards.greenhouse.io
"<T>" "remote (canada)" site:jobs.lever.co
"<T>" remote Canada site:myworkdayjobs.com
site:ca.indeed.com "<T>" remote
site:linkedin.com/jobs/view "<T>" "Canada" "Remote"
site:jobbank.gc.ca "<T>" "work from home"
site:weworkremotely.com "<T>" Canada
```
Hybrid:
```
"<T>" hybrid "<City>" apply
site:ca.indeed.com "<T>" hybrid "<City>"
site:linkedin.com/jobs/view "<T>" "<City>" "Hybrid"
site:boards.greenhouse.io "<T>" "<City>" hybrid
site:workopolis.com "<T>" hybrid "<City>"
```

## Canada-specific notes
- **Work rights** (Eligibility Gate): watch "legally entitled to work in Canada", "Canadian citizen or PR", Reliability / Secret / Top Secret clearance (federal roles often need citizenship or PR), and "no sponsorship available".
- **Remote caveats**: some "Remote Canada" roles require residence in a specific province (tax/payroll) or occasional office attendance; note this, don't drop it.
- **Salary**: Canadian postings often show CAD ranges (pay transparency laws in BC, and Ontario from Jan 2026); compare to `profile.md` minimum.
- **Quebec**: Bill 96 postings may require French; apply the Language Gate.

## Tuning
After a run, if results are mostly Weak/Poor, tighten tier 1 titles or add a domain keyword; if too few results, run `broad` or add more Canadian cities for hybrid.
