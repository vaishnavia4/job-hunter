---
name: job-batch
description: Run the daily job-hunter batch end to end - search job boards, screen each new job honestly, prepare folders, tailor resumes/cover letters (in parallel with subagents when there are many), apply in the browser with approval, and update the tracker and skills-gap report. Use when the user says "run today's batch", "apply to today's jobs", or "do my job search".
argument-hint: [max-jobs]
---

# Daily batch

Max jobs = $ARGUMENTS or `profile.job_preferences.applications_per_day`. Narrate each step briefly.

Pipeline: **find -> screen from `jobs/jd` -> folder only for "apply" -> tailor -> apply -> track.** Skipped jobs never get a folder.

1. **Find**: `search_jobs` (limit = max jobs). Options: `exclude_ats` (e.g. `["workday"]` for a quick run), `preferred_location=true` (only the profile's preferred locations or remote). Also run `read_job_alerts` if the user set up Gmail alerts, and `add_job` for any links they pasted (see "Paste a job link" below). Report fetched / passed / shortlisted and the top titles. If results look off (wrong functions, wrong countries), fix `config/search.json` regexes and re-run with `dry_run=true` first.
2. **Screen first, no folders yet**: for each `new` job read `jobs/jd/<id>.json` (JD text, score, flags) with `profile.json` and `master_resume.json`. Decide `apply` (honest fit % and gaps) or `skip` (reason). Record every skip in the tracker only: `update_status(job_id, {"status": "skipped", "notes": "<reason>"})`. **Do not create an application folder for a skip.** Skip rules are in the `tailor-resume` skill.
3. **Prepare only the "apply" jobs**: `prepare_application(job_id)` for each (one writer on the tracker), then write `decision.json` (`{"decision":"apply","fit":..,"gaps":[..]}`) in the folder and `sync_decisions`.
4. **Tailor**: follow the `tailor-resume` skill per folder. With more than ~5 folders, split them across 3-4 parallel helper agents and **start each with `model: "sonnet"`** (screening and tailoring do not need a larger model). Give each: the workspace path, its folder list, "read CLAUDE.md, master_resume.json, profile.json, and one finished example folder as the quality bar", the tailor-resume rules, and "don't edit the tracker, profile, config or other folders; don't open a browser". Cap 2 applies per company. When many jobs need screening, split the screening across helper agents the same way (they return decisions; you write the tracker).
5. Spot-check 1-2 resumes yourself (headline, page count, no gaps in the cover letter).
6. **Apply** one job at a time with the `apply` skill, best fit first, accountless ATSs (Greenhouse/Lever/Ashby) before Workday. Respect approval mode: say "ready" and wait.
7. **Wrap up**: `stats`, `skills_gap_report`; give the user a short table (applied / ready / blocked / skipped with reasons) and anything you need from them (verification emails, legal questions, new facts for the master resume). If older skipped folders exist, run `archive_skipped` (MCP) or `archive-skipped` (CLI) to move them to `applications/_skipped`.

## Email job alerts
`read_job_alerts(days?, dry_run?)` (CLI: `python -m jobhunter --workspace . alerts [--days N] [--dry-run]`) reads LinkedIn and Jobright alert emails over IMAP, opens each LinkedIn job's public guest page, scores it with the same rules as search and adds matches to the tracker as `new` (apply URL = the company's own site when LinkedIn exposes it). Jobright emails are listed as leads.

One-time setup for the user:
1. Turn on 2-Step Verification in the Google account, then create an **App Password** (Google Account > Security > 2-Step Verification > App passwords).
2. Put it in `profile/credentials.json`: `"gmail": {"email": "you@gmail.com", "app_password": "abcd efgh ijkl mnop"}` (local-only, gitignored; never echo it).
3. Create LinkedIn job alerts for the target titles (and optionally Jobright alerts); they arrive in Gmail.

Use `dry_run` first to see matches without touching the tracker. LinkedIn is discovery only: never apply through LinkedIn.

## Paste a job link
When the user pastes a posting URL (LinkedIn, Indeed, a company page, an ATS link):
1. `add_job(url, title?, company?, location?, description?)` (CLI: `add-job --url URL [--title --company --location --description-file]`). It fetches the page when it can, saves `jobs/jd/<id>.json` and a tracker row (`new`, scored).
2. If the page could not be fetched (login wall), ask the user to paste the title, company and description, and call `add_job` with them.
3. Screen it like any other job (step 2 above). On `apply`, `prepare_application` and tailor.
4. **Apply on the company's own site.** If the link is LinkedIn or Indeed it is discovery only: find the same posting on the company's careers page or ATS (search the title + company), update `apply_url` with `update_status`, and apply there.
