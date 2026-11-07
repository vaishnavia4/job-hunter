# Job Hunter workspace

This folder is one person's job search, run by the **job-hunter** plugin.
Pipeline: **search → screen → prepare → tailor → (approve) → apply → track → learn**.
Explain what you're doing as you go: the user should understand and learn the process.

## Files (all in this folder)
- `profile/profile.json`: every applicant answer (contact, work authorization rules, EEO, salary, start date, submit policy).
- `profile/master_resume.json`: **the only source of resume facts.**
- `profile/credentials.json`: local-only ATS passwords. Never echo, copy or log them.
- `config/search.json` (titles, locations, scoring), `config/skills.json` (skill vocab + have-flags), `config/companies.json` (boards), `config/learning.json`.
- `data/tracker.csv` (source of truth) → `tracker.xlsx` export. `skills_gap.md` = market demand vs. the user's skills.
- `applications/<Company>_<Role>/` (only for jobs you decided to apply to; skips live in the tracker): `job.json`, `JD.md`, `tailored.json`, `cover_letter.md`, resume + cover letter `.docx/.pdf`, `match_report.md`, `notes.md`, `decision.json`, screenshots.

Tools: the `jobs` MCP server (`search_jobs`, `read_job_alerts`, `add_job`, `archive_skipped`, `list_jobs`, `prepare_application`, `build_resume`, `update_status`, `sync_decisions`, `skills_gap_report`, `stats`): or the CLI `python -m jobhunter <cmd> --workspace .` from the plugin's `server/` folder.

## Non-negotiable rules
1. **Never invent anything.** No skill, tool, metric, title, date, employer or certification that is not in `master_resume.json`. Reword, reorder and select only. If the user tells you a new fact, add it to the master first (and ask when it conflicts with what's there).
2. **Headline = one job title** (the posting's title, cleaned up). No pipes, no skill lists.
3. **Cover letters never mention gaps.** Gaps go only in `match_report.md` / `notes.md`.
4. **Resumes are 2 pages max**, single column, standard headings. Bold 1–2 metrics per role. Truth check must PASS.
5. **Legal answers come from the user.** Work authorization wording follows `work_authorization.answer_rules` exactly. Arbitration agreements, conflict-of-interest and other attestations: fill only what the user has already answered; otherwise stop and ask. The user ticks legal-agreement boxes themselves.
6. **Approval mode:** until `submit_policy.approved_so_far` reaches `approve_first_n`, fill the form, screenshot it, set status `ready`, and tell the user "ready": they review and click Submit. Increment `approved_so_far` after each confirmed submission. Never click Submit while the user is still reviewing or while a file is being replaced.
7. **LinkedIn / Indeed Easy Apply = discovery only** (automation is against their terms). Apply on the company's own ATS.
8. Max 2 applications per company per day; never apply twice to the same job; never apply to excluded employers.
9. CAPTCHA, SMS codes, or anything needing the user's personal password (e.g. Google) → status `blocked`, tell the user, move on.

## Daily flow (see the `job-batch` skill)
1. `search_jobs` (also refreshes `skills_gap.md`).
2. **Screen from `jobs/jd/<id>.json` first (no folder yet).** Decide `apply` (honest fit % and gaps) or `skip` (reason). Record skips in the tracker only (`update_status` status=skipped + notes). **Create the application folder only for `apply`** (`prepare_application`), then write `decision.json` and `sync_decisions`. Old skipped folders: `archive-skipped` moves them to `applications/_skipped`.
3. Write `tailored.json` + `cover_letter.md` -> `build_resume` until 2 pages + PASS. Verify the headline in the PDF before uploading. When delegating screening or tailoring to helper agents, start them with `model: "sonnet"`.
4. Apply with the browser (see the `apply` skill playbooks) → screenshot → `update_status`.
- **Answer bank (mandatory):** before filling ANY application field call `lookup_answer` (MCP `mcp__plugin_job-hunter_jobs__lookup_answer`, or `python -m jobhunter --workspace . answer "<label>" --options "a|b|c"` from the job-hunter plugin's `server/` folder) and use its answer; every time the user corrects or fills an answer, `record_answer(..., source="user_correction")`; before saying "ready", read back all fields and run `verify_form` until it passes, then do the FINAL CHECK: on the Review page / filled form read back all values + page text and call `final_review` (CLI `final-check <review.json>`) until `ok` is true; show the summary table and only then say "ready to submit" (also before any auto-submit). Answers live in `profile/answer_bank.json`.
- **Manual links:** when the user pastes a job URL, `add_job` it, screen it, and apply on the company's own site (LinkedIn/Indeed links are discovery only). **Alerts:** `read_job_alerts` reads LinkedIn/Jobright alert emails (needs a Gmail App Password in `profile/credentials.json`).
