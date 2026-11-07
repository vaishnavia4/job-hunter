# jobhunter.fill - token-free form filler

Fills an application with Python Playwright (no model tokens), never clicks Submit, prints a JSON summary.
Claude only handles `unknown_questions` / essays afterwards.

    pip install playwright && python -m playwright install chromium

    cd server
    python -m jobhunter.fill --workspace WS --folder applications/Company_Role [--url URL] [--headed false] [--profile-dir DIR] [--keep-open]

- Browser: Chromium persistent context at `<WS>/.browser-profile` (logins/cookies persist). Headed by default.
- Inputs from the folder: `job.json` (url/apply_url, company, source), `Resume_*.pdf`, `CoverLetter_*.pdf`.
- Every question goes through `answers.lookup(ws, label, options)`; unknown/ambiguous/essay -> `unknown_questions`, field left blank.
- Legal consent/arbitration/terms boxes are left unticked and reported in `problems` as "user must tick".
- ATS: greenhouse, ashby, lever, workday (detected from URL). Workday uses `profile/credentials.json` (`workday`, else `job_sites_default`).
- Workday auth is a state machine: sign in -> Create Account -> activation email (IMAP, `jobhunter.mail_verify`) -> sign in,
  or Forgot password -> reset email -> sign in. Stops with `auth_state` = `needs_user_checkbox` (the user ticks the
  agreement), `captcha`, `maintenance`, `signin_failed`, `verification_missing`, `reset_failed`. Links and the password are
  never logged. Needs `gmail.email` + `gmail.app_password` in credentials.json for the email steps.
- Workday My Experience: resume first (by label), then work history, education (exact school option only),
  certifications (earned only; empty rows and empty "Specialties N" groups deleted), skills, websites.
- Output: JSON `{ats, steps_completed, filled, unknown_questions, problems, screenshot, auth_state?, readback?}`; also saved
  as `fill_report.json` in the folder. `readback` maps each Workday step to the values read back from the page.
- Offline tests for the pure helpers: `python tests/test_fill_workday.py` (from `server/`).
