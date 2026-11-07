---
name: job-setup
description: Set up job-hunter for a new person from their resume - creates the workspace, parses the resume into a truthful master resume, interviews the user only for what the resume lacks (contact, work authorization/sponsorship, EEO, salary, locations, exclusions, approval mode), and generates role-specific search titles, skill vocabulary and learning paths for ANY role (engineer, business analyst, data analyst, PM, etc.). Use when the user says "set up job hunter", uploads a resume to start, or the workspace has no profile yet.
argument-hint: [path-to-resume]
---

# Job-hunter setup

Goal: in one sitting, turn a resume into a ready workspace. Work in the folder Claude Code is open in (the WORKSPACE). Tell the user what you're doing at each step.

## 1. Create the workspace
Call `init_workspace` (MCP tool `mcp__plugin_job-hunter_jobs__init_workspace`, workspace = absolute path of the current folder). It never overwrites existing files. If the MCP server isn't available, run `python -m jobhunter init --workspace "<folder>"` from `${CLAUDE_PLUGIN_ROOT}/server`, and tell the user to `pip install -r ${CLAUDE_PLUGIN_ROOT}/requirements.txt`.

## 2. Read the resume
Resume path = $ARGUMENTS, or ask for it (any .docx/.pdf/.txt). Call `extract_resume_text`. If they have several resumes or a LinkedIn PDF, read all and merge — note conflicts (dates, titles, years) and ask which is right.

## 3. Write `profile/master_resume.json` (source of truth)
Fill the template schema from the resume ONLY: name, contact, every employer with exact title/dates/location, all bullets (reworded only for clarity, metrics exact), skills grouped by category (`primary`; lighter ones in `secondary`), certifications (mark in-progress), education. Do not invent. Put anything uncertain in `_pending_confirmation` and ask.

## 4. Interview — the FULL question bank, up front (so applying never stops to ask)
Real application forms ask ~80 recurring questions in many wordings. Walk the user through **every** entry in
`${CLAUDE_PLUGIN_ROOT}/templates/question_bank.json` (skip `per_job_essay` ones), category by category, so nothing
has to be guessed or corrected later. Rules:
- **Batch by category** (identity & contact → location & work mode → work authorization → employment history &
  conflicts → compensation & availability → education & experience → EEO → consents → legal). One message per
  category: a table of *question → proposed answer*, pre-filled from the resume where possible ("Current employer:
  Acme Corp"), blanks where only they can answer. They confirm or correct the whole batch at once.
- **Work authorization — every wording separately**, in their words, never decided for them: legally authorized;
  "for any employer"; "unrestricted"; "without sponsorship"; sponsorship **now** vs **in the future** vs combined;
  multiple-choice status (any employer / current employer / seeking); visa type; OPT/EAD; citizenship; clearance.
  (Example: an H-1B holder may answer legally authorized = Yes, any employer = Yes, unrestricted = No,
  sponsorship now = Yes, future = Yes, status = Can work for any employer — but it is THEIR call.)
- **Conflicts / legal — ask each explicitly** (no assumed defaults): previously worked at the company / as a
  contractor / at a dealer-affiliate, relatives or relationships at the company, auditor independence (them or family
  at the company's auditor), federal/regulator employment in the last 2 years, non-compete or restrictive agreements,
  confidential info from a prior employer, outside business / board roles, dual-employment acknowledgement,
  real-estate license, security clearance. Arbitration agreements and "I certify" attestations: explain they will
  tick these themselves (`USER_MUST_TICK`) unless they explicitly give a standing answer.
- **EEO with subgroups**: gender, pronouns, Hispanic/Latino, race **and subgroup** (e.g. Asian → South Asian (Indian)),
  veteran, spouse/military service, disability, sexual orientation, transgender — each may be "decline".
  For `options_hint` list the most specific regex first and avoid words that also appear in other groups
  (e.g. not "Indian" — it matches "American Indian"; use "south asian").
- **Policies**: GitHub/portfolio — always, only when required, or never; degree/school — fill even when optional?;
  graduation year only when required?; current salary (many states ban the question — default skip); desired base +
  total comp + bracket rule; SMS/WhatsApp consent; how-did-you-hear default; years per skill; honest phrasing for
  "years of software engineering" if their background is adjacent.
- **Save** every answer with `record_answer(source="setup")` (or write `profile/answer_bank.json`:
  `{"answers":[{"id":"<question id>","answer":"...","options_hint":["..."],"note":"...","source":"setup"}]}`) **and** the
  matching `profile/profile.json` fields. Then spot-check `lookup_answer` on 5 tricky real labels (e.g. "Are you
  legally authorized to work … for any employer?", a race dropdown with subgroups, a salary-bracket dropdown) and run
  `python server/tests/test_answers.py <workspace>` from the plugin folder if they share the same answers.
Also ask (profile.json only): target roles (suggest 8–15 titles incl. adjacent ones) and seniority, preferred
locations/remote, excluded employers (current employer!), applications per day, approve-first-N (default 10).

## 5. Generate role-specific config
- `config/search.json`: `title_include_regex` covering every target title family; `title_exclude_regex` (internships, unrelated functions, too-senior); `title_bonus_regex` for the best-fit titles; `seniority_penalty_regex`; `workday_queries` (4–8 short search phrases for their roles, e.g. "business analyst", "systems analyst"); `location.preferred` = [{"regex": "(denver|colorado|, co\\b)", "bonus": 20, "label": "Colorado"}]; `keyword_bonus` for their signature skill (e.g. {"regex":"\\bsalesforce\\b","title":8,"desc":4}). Keep the default country/exclude regexes unless they search outside the US (then rewrite them).
- `config/skills.json`: start from `config/skills_library.json` — copy the relevant categories' entries into `{"vocab": {...}}`, add any important skills from the resume or target JDs that are missing, and set `have`: `true` (clearly on the resume), `"secondary"` (mentioned lightly), `false` (gap). Aim for 40–90 entries.
- `config/companies.json`: keep the defaults, and add employers that hire their role in their preferred locations (banks, insurers, healthcare, consulting, government contractors, local companies). Workday boards need `tenant.wdN/SiteName`. Run `validate_boards` and drop dead ones.
- `config/learning.json`: add entries for their likely gap skills: `"Skill": [why it matters, how to learn (free first), proof project]`.

## 6. Confirm and first run
Show a short summary (target titles, locations, salary rules, work-auth rules, approval mode). Then run `search_jobs` with `dry_run=true` to show the top matches; tune regexes if results are off; then a real `search_jobs`. Offer to schedule it daily (Windows Task Scheduler / cron running `python -m jobhunter search --workspace <folder>`).

Remind the user: everything stays in this folder on their machine; `profile/credentials.json` is only for ATS accounts they choose to let Claude create.
