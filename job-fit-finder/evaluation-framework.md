# Job Evaluation Framework

Adapted from the open-source `ai-job-search` evaluation framework (framework version 1.2.7). Gates run first; scoring only applies to postings that pass.

## Eligibility Gate (hard filter, before scoring)

Read the posting's eligibility / work-rights section **verbatim** and classify against `profile.md` Section 3:

| Posting wording | Verdict |
|---|---|
| Requires citizenship or permanent residency you don't hold ("must be a citizen", "PR required", or "full working rights" where citizen/PR is meant) | **FAIL - hard stop.** Do not score. Quote the exact wording. |
| Requires a security clearance | **FAIL** in most cases (clearances are usually citizenship-gated). Verify the scheme before assuming. |
| Explicitly names your permit class, or says "international applicants welcome", "visa holders considered", "we sponsor" | **PASS** - verified. |
| Silent on citizenship/residency | **PROCEED, mark UNVERIFIED.** Recommend checking the employer's careers/international page. |

Rules that are easy to get wrong:
1. **Silence is not permission.** Large graduate programs, professional-services firms, government/defence, banking, telecoms and critical infrastructure often gate eligibility off-ad.
2. **A company-wide "we accept international applicants" is not role-level permission.** Confirm the specific posting or stream is on the named list.
3. Always report an eligibility failure with the quoted source rather than silently dropping the role; the user may know something the profile doesn't.
4. If the candidate's permit also limits hours or start date, treat it as a second, separate gate (timing vs. eligibility fail for different reasons).

Skip this gate entirely if `profile.md` says the user is a citizen/PR of the posting's country.

## Language Gate (hard filter, before scoring)

Read language requirements for **the role itself** (job-condition requirements), not the language the ad is written in.

| Posting requirement vs. your Languages table | Verdict |
|---|---|
| A language **not on your table at all** ("fluent Polish required") | **FAIL - hard stop.** Quote the requirement line. |
| A language you list, but the stated bar ("fluent", "native", "C1+") plausibly exceeds your declared level | **FLAG, then proceed.** Score it, show both quotes, let the user judge. |
| A language you list, at/below your level, or no level stated | **PASS.** |

Judge levels by reading both sides as written (CEFR, LinkedIn buckets and plain words don't map precisely). When unsure, prefer FLAG over a silent PASS.

## Scoring Dimensions

### 1. Technical Skills Match (0-100) - weight 30%
| Score | Meaning |
|---|---|
| 80-100 | Core requirements are your primary skills |
| 60-79 | Most match, 1-2 learnable gaps |
| 40-59 | Partial match, significant upskilling needed |
| 0-39 | Fundamental mismatch |

### 2. Experience Match (0-100) - weight 25%
Match on the function and nature of work performed, not job title (a "Data Consultant" and "Data Scientist" can be the same job).
| Score | Meaning |
|---|---|
| 80-100 | Direct experience, same domain and role type |
| 60-79 | Related experience, transferable skills clear |
| 40-59 | Adjacent; you'd need to make the case |
| 0-39 | Unrelated |

### 3. Behavioral / Culture Fit (0-100) - weight 15%
Based on the behavioral section of `profile.md` and signals in the posting (autonomy, pace, team structure, management language, on-call/travel, maintenance vs. building).
| Score | Meaning |
|---|---|
| 80-100 | Strongly matches preferences |
| 60-79 | Mixed but mostly compatible |
| 40-59 | Some friction |
| 0-39 | Significant mismatch |
Red flags to note: disorganized-sounding department, work dominated by maintenance, vague scope, "fast-paced/wear many hats" with no substance, unrealistic requirement stacks.

### 4. Location & Logistics (Pass / Fail / Flag) - not weighted
Scope is **Remote or Hybrid, in Canada only**.
- Remote and open to people living in Canada: PASS
- Hybrid with a Canadian office within reach of your home base (or a city you'll relocate to): PASS
- Hybrid where in-office days exceed your limit in `profile.md`, or heavy travel: FLAG
- Fully on-site, outside Canada, or remote restricted to another country/US state: FAIL (out of scope, not scored)
- Remote but requires residence in a specific province, or occasional office attendance: FLAG with details

### 5. Career Alignment & Motivation (0-100) - weight 30%
Does the role advance your stated goals and contain tasks that energize rather than drain? Also weigh your life-situation constraints (security, flexibility, development priorities) from `profile.md`.
| Score | Meaning |
|---|---|
| 80-100 | Strong alignment, clear growth path |
| 60-79 | Good role, partial alignment with long-term goals |
| 40-59 | Decent job that doesn't build toward goals |
| 0-39 | Dead end or backwards step |

## Deal-breaker veto
Any deal-breaker listed in `profile.md` Section 4 that the posting clearly triggers caps the verdict at **Poor Fit** regardless of numeric score and is reported as a veto with the quoted line.

## Weighting
Overall = 0.30 x Technical + 0.25 x Experience + 0.15 x Behavioral + 0.30 x Career. (Location is pass/fail and unweighted.)

## Thresholds
- **Strong Fit** 75+: definitely apply
- **Good Fit** 60-74: apply, address gaps
- **Moderate Fit** 45-59: consider carefully
- **Weak Fit** 30-44: probably skip unless strategic
- **Poor Fit** <30: skip

## Output Format (single posting)

```
## Job Fit Evaluation: [Role] at [Company]
[Posting URL] | [Location] | [Posted/Deadline if known]
[Gate flags / injection warning, if any]

| Dimension | Score | Notes |
|-----------|-------|-------|
| Technical Skills | XX/100 | [brief note] |
| Experience Match | XX/100 | [brief note] |
| Behavioral Fit | XX/100 | [brief note] |
| Location | PASS/FAIL/FLAG | [brief note] |
| Career Alignment | XX/100 | [brief note] |

**Overall Score: XX/100**

### Verdict: [Strong / Good / Moderate / Weak / Poor] Fit

### Key Strengths for This Role
- ...

### Gaps to Address
- ...

### Recommendation
[1-2 sentences: apply / skip / apply with caveats]

### Before You Apply (optional)
- Eligibility status: PASS / UNVERIFIED (what to check) 
- Questions worth asking the recruiter, if the posting is vague about priorities or day-to-day tasks
```

## Calling the employer (best practice, only when useful)
Suggest it only when the posting has unclear requirements, vague day-to-day tasks, or a named contact inviting questions. Never to "be remembered". Good questions: primary challenges of the role; how time is split across responsibilities; most critical competencies; what success looks like in 6-12 months.
