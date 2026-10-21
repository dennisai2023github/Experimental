# job-fit-finder (local skill)

Search for postings and rate your fit. No GitHub, no scrapers, no LaTeX, no drafting.

## Install (Claude Code, Windows)
Copy this whole folder to `%USERPROFILE%\.claude\skills\job-fit-finder\`
(macOS/Linux: `~/.claude/skills/job-fit-finder/`). For a single project, use `<project>\.claude\skills\job-fit-finder\` instead.

## Use
1. Open `claude`. First run: say "find jobs" - the skill interviews you and fills `profile.md` (or edit that file yourself, or paste your CV).
2. Rate one job: `/job-fit https://...` or paste the posting text.
3. Search: "find jobs in Toronto remote data governance".
4. Rank a batch: paste several URLs and say "rank these".

Optional flags: `broad`, `--limit N`, `--top N`, `--all`.

## Credit
Scoring model (gates, five dimensions, weights, thresholds) adapted from the open-source MadsLorentzen/ai-job-search framework.
