---
name: apply
description: Fill and submit a job application in the browser (Playwright MCP) for a tailored job-hunter application - playbooks for Greenhouse, Lever, Ashby and Workday (account creation, email verification, dropdowns, dates, skills), work-authorization wording rules, approval mode (user clicks Submit for the first N), screenshots and tracker updates. Use when asked to apply to a job or during job-batch's apply step.
argument-hint: [application-folder-or-job-id]
---

# Apply to a job

Needs the Playwright MCP browser tools. Read `profile/profile.json` (`submit_policy`), the folder's `notes.md`, `decision.json`, the built resume/cover-letter PDFs, and **`lessons.md` in this skill folder** (mistakes already made once: don't repeat them).

## The answer loop (mandatory: this is what makes it rigid)
1. **Every question → `lookup_answer(question_text, options)`** with the exact label and the *visible* option texts.
   - `ok` → fill `option` (dropdown/radio) or `answer` (text). Never improvise a different answer.
   - `ASK_USER` / `AMBIGUOUS_OPTIONS` → ask the user (batch all unknowns of the form in one message), fill it, then
     `record_answer(question_text, answer, source="user_correction", options_hint=[most specific regex first])`.
   - `USER_MUST_TICK` → leave it; list it in the "ready" message for the user to tick.
   - `GENERATE_ESSAY` → write a short truthful answer from `master_resume.json` + the JD; save it in `notes.md`.
2. **User fixes are training data.** Whenever the user says an answer is wrong, or you notice on the page that they
   changed/filled a field themselves (read back values before and after their review), immediately call
   `record_answer(..., source="user_correction" | "user_filled")`. Next time `lookup_answer` returns their answer.
3. **Before saying "ready"**: read back EVERY field from the page: label, current value/selected option, the visible
   options, required flag, and for uploads `kind:"file"` + the file name in each box. Then call `verify_form(fields)`.
   Fix every `mismatches` / `missing_required` / `file_problems`, confirm `unknown` with the user (then record), and
   re-run until `pass: true`.
4. **FINAL CHECK (mandatory, last step before "ready to submit" or any Submit click).** The user wants one last
   re-verification of everything. On the **Review page** (multi-page ATS such as Workday) or the **filled single-page
   form** (Greenhouse/Lever/Ashby): read back ALL values (label, selected value, required flag, uploaded file names
   per upload box) plus the full visible page text, then call
   `final_review({company, title, fields:[{label,value,required,kind?}], files:[names], page_text})`
   (CLI: `python -m jobhunter --workspace . final-check review.json`). It re-runs `verify_form`, checks every required
   field, that the resume file name contains the company and the cover-letter slot holds a CoverLetter file, and scans
   the text for red flags (Bachelor of Arts, "I identify as a veteran", wrong race/gender, future end date on the
   current job, wrong salary, "Error"). Fix every item in `problems` (go back to the step, change it, return to Review),
   and re-run until `ok: true`. THEN show the user the `summary_table` and only then say **"ready to submit"**
   (or click Submit yourself once `approved_so_far >= approve_first_n`). Never skip this, even for auto-submit.

## Universal rules
- **Skip option dumps for phone-country and Country lists** (200+ entries). Type the country (the one in `profile.json`) and click the visible match; don't read all options.
- **Upload the resume FIRST**, then fill fields (some ATSs autofill from the file and overwrite answers).
- **Verify the PDF is the current build** (headline = single title) before uploading. If you rebuild after uploading, replace the file.
- Fill from `profile.json` only. Free-text questions: short, specific, truthful answers built from `master_resume.json`; save them in `notes.md`. Don't overstate (e.g. "most of the team left" when the master says "a team transition").
- **Work authorization**: follow `answer_rules` literally for each wording ("legally authorized", "for any employer", "unrestricted", "without sponsorship", "require sponsorship now or in the future"). If a new wording appears, stop and ask.
- **Legal attestations** (arbitration agreements, "I certify…", conflict-of-interest, non-compete, relatives at company): use only answers the user already gave; otherwise ask. Leave arbitration/legal-agreement checkboxes for the user to tick.
- EEO: use `eeo_voluntary`; pick the option that matches, else "Decline". When a dropdown offers **subgroups** (e.g. "Asian: East Asian / South Asian / Southeast Asian"), match `race_detail` precisely: never take the first option that merely contains the word. Pronoun fields: use `eeo_voluntary.pronouns`.
- **Required-field check before "ready"**: every field marked `*` / `aria-required` must have a value (GitHub/portfolio links included when required). Read back the selected values and compare to the profile.
- Dropdown option pickers: only consider **visible** options (`[role=option]:visible`): hidden lists (e.g. phone country-code pickers) otherwise get matched.
- Salary: `desired_salary_answer`; for range dropdowns pick the bracket that contains/starts at it.
- Location/relocation: from `job_preferences`.
- Preferred name fields: fill them.
- Never apply through LinkedIn/Indeed Easy Apply. CAPTCHA / SMS / personal-account passwords → status `blocked`, tell the user.
- **Approval mode**: while `approved_so_far < approve_first_n` → fill everything, full-page screenshot to the folder (`review_before_submit.png`), `update_status(status="ready")`, run the FINAL CHECK, then tell the user **"ready to submit"** with the `summary_table` of the answers. Don't touch the page while they review. After they confirm it's submitted (check the confirmation page), screenshot it, `update_status(status="applied", submitted_by="user")`, increment `approved_so_far` in profile.json. After N approvals, click Submit yourself and record `submitted_by="claude"`.
- Always record answers in `notes.md` and add a dated line to the status log.

## Greenhouse (job-boards.greenhouse.io / boards.greenhouse.io)
Single page. **Separate "Resume/CV" and "Cover Letter" file inputs: upload by LABEL** (find the input inside the field whose label says Resume/CV for the resume, Cover Letter for the cover-letter PDF); never use "the first `input[type=file]`". Then verify each box shows the right file name. Text fields, custom questions as selects/radios, EEO block at the bottom. Some have reCAPTCHA on submit. Location may be an autocomplete: type, wait, pick the matching option. Fill School/Degree even when optional if the answer bank says so.
New UI (job-boards.greenhouse.io): almost every question is a react-select combobox (`input` with a label `for=`): click it, optionally type, then click a **visible** `[role=option]`. Option texts are long ("I have not worked at <Company>", "United States +1"): match by meaning, not just "No"/"Yes". Sponsorship is often split into "now" and "in the future"; an H-1B transfer needs sponsorship **now** too.

## Lever (jobs.lever.co/<co>/<id>/apply)
Single page: resume upload first (it may prefill name/email), "Additional information" textarea is a good place for a 2–3 sentence pitch, custom questions, EEO block. hCaptcha may appear on submit → user.

## Ashby (jobs.ashbyhq.com/<co>/<id>/application)
- There may be TWO file inputs: an "Autofill from resume" one and the real **Resume** field: use the one whose field label is "Resume".
- Yes/No questions are **buttons** inside the field (hidden checkbox): click the "Yes"/"No" button in that field's container.
- Location is a combobox (`input.ashby-application-form-input-autocomplete`): type slowly, wait ~2 s, click the first `[role=option]`.
- Radios: `getByLabel(exact option text).check()`.
- After a file is uploaded the input disappears; replace via the field's "Replace" button.
- Success page shows "Your application was successfully submitted" / "Success".

## Workday (<tenant>.wdN.myworkdayjobs.com)
The token-free filler encodes all of this: `python -m jobhunter.fill --workspace WS --folder applications/<Company_Role>`
(from `server/`). Its JSON report has `auth_state`, `problems`, `unknown_questions` and a per-step `readback`.
- Use **Apply Manually** (not "Autofill with Resume"). A Workday account is **per company tenant**.
- **Auth chooser:** when Apple / Google / LinkedIn / email buttons show, click **"Sign in with email"**.
- **Sign-in form:** submit with `[data-automation-id=signInSubmitButton]` (force-click; a `click_filter` layer may cover it).
  The header "Sign In" is only a nav link. Never fill the hidden `beecatcher` input (honeypot).
- **Account flow (state machine, never stop at "sign-in rejected"):**
  1. Sign in once.
  2. "Wrong email or password" / "locked" -> **Create Account** via the form's own link (`createAccountLink` or
     `click_filter[aria-label="Create Account"]`): email, password, `verifyPassword`. If a `createAccountCheckbox`
     (I agree) is shown, **stop and report `needs_user_checkbox`**: consent belongs to the user.
     Submit with `createAccountSubmitButton`.
  3. Read the activation email over IMAP (`jobhunter.mail_verify.find_verification(ws, host_hint="<tenant>.wdN")`,
     CLI `python -m jobhunter mail-verify --host <tenant>.wdN --wait 180`). The link contains `/activate/`; only open it
     if it is https on the same tenant. Never print or log the link. Then sign in again (the one allowed retry).
  4. "Already exists", or no activation email within ~3 minutes and the page is back at Sign In (= existing account) ->
     **Forgot your password?** -> submit the email -> reset email (link contains `/passwordreset/`) -> set the configured
     password -> sign in.
  5. Stop (and report) on CAPTCHA, maintenance, a failed reset, or sign-in still rejected after activation/reset.
     Never retry the same password twice in a row: repeated failures lock the account.
- **Passwords:** never put them in Playwright code (tool calls are printed into the transcript). Serve them from a
  one-shot local credential server that the browser script fetches; never snapshot a page with a filled password field.
- Steps: My Information -> My Experience -> Application Questions (1-2 pages) -> Voluntary Disclosures -> (Self Identify) -> Review.
- Dropdowns are `button[aria-haspopup]`: click, wait ~700 ms, click the `[role=option]` with the exact text.
- **"How did you hear"** is a 2-level multiselect (`[data-automation-id=promptOption]`): pick the category, then the item
  (e.g. Website -> "<Company> Careers Website"), from the answer bank's answer, else the careers-site default (LinkedIn when
  the job came from LinkedIn). Verify a `selectedItem` pill appears.
- **My Experience order:** upload the resume FIRST (by label: the "Resume/CV" group), then Work Experience, Education,
  Certifications, Skills, Websites, so a later failure cannot leave the resume missing.
- **Added rows are separate groups** ("Work Experience 2", "Websites 1", "Certifications 2"). Find the section's
  Add / Add Another button relative to its heading, then fill inside the numbered group.
- Work experience: tick "I currently work here" for the current job (no end date); dates are spinbuttons: type `MMYYYY`
  slowly into the Month box (it auto-advances).
- **School:** type the school from the master resume, then scroll the virtualized list until an option **equals** the
  school name (case-insensitive). Lists hold many "<College>, <University>" entries: never take the first partial match.
  If no exact option exists, leave it and report it.
- Degree (dropdown, never "Bachelor of Arts" unless that is the degree), Field of Study (search prompt).
- **Certifications:** earned certifications only (no in-progress ones). Search the prompt and pick the matching option
  (same name or exam code). If nothing matches, delete that empty row. Picking a certificate can spawn an empty,
  required **"Specialties N"** group: delete it, or Save fails.
- Websites / Social Network URLs: fill the profile's website, GitHub and LinkedIn (LinkedIn in its own field when present).
- Skills: type each skill, Enter, click the matching prompt option.
- **Read back after every step:** visible values, `selectedItem` pills and error messages; fix every empty required field
  before Save and Continue. Drafts can lose Voluntary Disclosures answers: always re-read that step.
- "Workday is currently unavailable" = maintenance window: stop and retry later; don't mark the job blocked.
- **Prefer "Sign in with Google"** only when the user allows it; they type their own Google password.
- Candidate Home shows status after submission ("Review", "Date Submitted").

## SmartRecruiters / company sites
Same universal rules; snapshot the form, map fields by label, screenshot before submit.
