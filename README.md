# job-hunter — a Claude Code plugin for job searching (any role)

Give it your resume. Claude then:

1. **Searches** ~190 company job boards daily (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Remotive) and scores each job against *your* titles, locations, salary floor, skills and visa needs (it auto-skips "no sponsorship", citizenship/clearance-only jobs if they apply to you).
2. **Screens** each job honestly (fit %, real gaps) and skips poor fits with a written reason.
3. **Tailors** a 2-page ATS-friendly resume + cover letter per job — rewording and reordering only what's true in your master resume (a truth check blocks invented skills, titles, dates).
4. **Applies** in the browser (Greenhouse, Lever, Ashby, Workday playbooks) — you approve and click Submit for the first N (default 10).
5. **Tracks** everything in `tracker.xlsx` (one folder per application with the JD, resume, cover letter, answers, screenshots).
6. **Shows your skills gap**: which skills the market asks for that you don't have, with free learning paths and proof projects.

Works for any role: engineers, business analysts, data analysts, product/project managers, security, etc. — setup generates role-specific titles, search terms and skill vocabularies from your resume.

## Install (for you or a friend)

Prerequisites
- [Claude Code](https://code.claude.com) and Python 3.10+
- `pip install -r requirements.txt` (from this folder)
- For PDFs: Microsoft Word (Windows) or LibreOffice (any OS). Without either you get .docx only.
- For applying: the Playwright MCP server in Claude Code (`claude mcp add playwright npx @playwright/mcp@latest`)

Add the marketplace and install (inside Claude Code):
```
/plugin marketplace add vaishnavia4/job-hunter      # or a local folder path
# (local copy: /plugin marketplace add C:/path/to/job-hunter)
/plugin install job-hunter@job-hunter-marketplace
```
Then `/reload-plugins` (or restart) and check `/mcp` shows `plugin:job-hunter:jobs`.

## First run
1. Make an empty folder for your job search (e.g. `C:\Users\you\job-search`) and open Claude Code in it.
2. Run `/job-hunter:setup C:\path\to\your-resume.pdf` — Claude reads the resume, asks only what it can't know (work authorization, salary, locations, EEO choices, approval count), and writes your profile and config.
3. `/job-hunter:search` to see matches, then `/job-hunter:batch` to tailor and apply.
4. `/job-hunter:status` any time; `/job-hunter:gap` for what to learn next.

Optional daily schedule (Windows Task Scheduler / cron):
`python -m jobhunter search --workspace "C:\Users\you\job-search"` (run from this plugin's `server/` folder).

## Commands & tools
| Slash command | What it does |
|---|---|
| `/job-hunter:setup [resume]` | Create workspace from a resume (skill `job-setup`) |
| `/job-hunter:search [n]` | Search boards now, show top matches |
| `/job-hunter:batch [n]` | Full daily run: search → screen → tailor → apply → track |
| `/job-hunter:status` | Tracker counts, jobs ready for your Submit, blocked items |
| `/job-hunter:gap` | Skills gap + learning plan |

Skills: `job-setup`, `tailor-resume`, `apply`, `job-batch`, `skills-gap`.
MCP tools (`mcp__plugin_job-hunter_jobs__*`): `init_workspace`, `extract_resume_text`, `search_jobs`, `validate_boards`, `list_jobs`, `prepare_application`, `build_resume`, `update_status`, `sync_decisions`, `skills_gap_report`, `stats`.
CLI (same engine): `cd server && python -m jobhunter --workspace <folder> <init|extract|search|list|prepare|build|set|sync|skills-gap|stats>`.

## Privacy
Everything stays in **your** workspace folder on your machine: resume, answers, EEO choices, tracker, and `profile/credentials.json` (ATS passwords, only if you choose to let Claude create Workday accounts). Nothing is uploaded anywhere except the job applications you approve. The workspace `.gitignore` keeps personal files out of git. Don't share your workspace folder — share only this plugin.

## Honesty & terms
- The truth check refuses resume content that isn't in your master resume. Fabrication gets offers rescinded; this tool won't do it.
- You answer every legal question (work authorization wording, arbitration agreements, conflicts of interest) once during setup or when first asked; you tick legal-agreement boxes yourself.
- LinkedIn and Indeed prohibit automated applying — the plugin uses them only to discover jobs and applies on the company's own site.
- CAPTCHAs, SMS codes and your Google/LinkedIn passwords are always yours to enter.

## Layout
```
job-hunter/
  .claude-plugin/plugin.json, marketplace.json
  .mcp.json                      -> python server/server.py (MCP "jobs")
  server/server.py, server/jobhunter/   (workspace, search, resume, applications, skills_gap, extract, cli)
  skills/<job-setup|tailor-resume|apply|job-batch|skills-gap>/SKILL.md
  commands/<setup|search|batch|status|gap>.md
  templates/ (CLAUDE.md, profile, master resume, credentials, config defaults incl. ~190 job boards, skills library, learning paths)
```

## Answer bank: it learns your answers once
Application forms repeat about 80 questions in dozens of wordings ("legally authorized" vs "for any employer" vs
"unrestricted", sponsorship "now" vs "in the future", race dropdowns with subgroups, salary brackets…).
- `/job-hunter:setup` walks you through **all** of them once (`templates/question_bank.json`) and saves your answers
  in `profile/answer_bank.json`.
- While applying, Claude calls `lookup_answer` for every field. When you correct something, it calls `record_answer`,
  so your correction wins from then on. Before telling you "ready", it reads the form back and runs `verify_form`,
  which catches wrong options, empty required fields, and a resume uploaded into the cover-letter box.
- Arbitration agreements and "I certify" boxes are always left for you to tick.
- Regression tests: `python server/tests/test_answers.py` (fictional sample) or `python server/tests/test_answers.py <your-workspace>`.
