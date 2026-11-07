---
name: job-batch
description: Run the daily job-hunter batch end to end - search job boards, screen each new job honestly, prepare folders, tailor resumes/cover letters (in parallel with subagents when there are many), apply in the browser with approval, and update the tracker and skills-gap report. Use when the user says "run today's batch", "apply to today's jobs", or "do my job search".
argument-hint: [max-jobs]
---

# Daily batch

Max jobs = $ARGUMENTS or `profile.job_preferences.applications_per_day`. Narrate each step briefly.

1. **Search**: `search_jobs` (limit = max jobs). Report fetched / passed / shortlisted and the top titles. If results look off (wrong functions, wrong countries), fix `config/search.json` regexes and re-run with `dry_run=true` first.
2. **Prepare**: `prepare_application` with `next_n` = max jobs (one call, so only one writer touches the tracker).
3. **Screen + tailor**: follow the `tailor-resume` skill per folder. With more than ~5 folders, split them across 3–4 parallel subagents. Give each subagent: the workspace path, its folder list, "read CLAUDE.md, master_resume.json, profile.json, and one finished example folder as the quality bar", the tailor-resume rules, and "don't edit the tracker, profile, config or other folders; don't open a browser". Cap 2 applies per company.
4. `sync_decisions`. Spot-check 1–2 resumes yourself (headline, page count, no gaps in the cover letter).
5. **Apply** one job at a time with the `apply` skill, best fit first, accountless ATSs (Greenhouse/Lever/Ashby) before Workday. Respect approval mode — say "ready" and wait.
6. **Wrap up**: `stats`, `skills_gap_report`; give the user a short table (applied / ready / blocked / skipped with reasons) and anything you need from them (verification emails, legal questions, new facts for the master resume).
