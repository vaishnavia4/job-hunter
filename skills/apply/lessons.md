# Lessons learned on real applications (read before every apply)

Each line is a mistake that already happened once. Don't repeat it.

## Files
- **Upload the resume first**, then fill the form. Some ATSs autofill from the file and overwrite answers.
- **Verify the PDF build before uploading.** Open the PDF and check page count (2) and headline (one job title, no pipes). A resume uploaded before a rebuild must be replaced.
- **Upload by label, never by position.** Greenhouse has separate "Resume/CV" and "Cover Letter" inputs; Ashby has an "Autofill from resume" input plus the real "Resume" one. Afterwards read back the file name shown in each box; `verify_form` flags a Resume_* file in the Cover Letter box.
- **Ashby:** after upload the input disappears; use the field's "Replace" control to swap files.

## Answers
- **Call `lookup_answer` for every question, even ones that look obvious.** Wordings that look alike have different answers: "legally authorized" vs "for any employer" vs "unrestricted" vs "without sponsorship"; sponsorship "now" vs "in the future" vs combined; multiple-choice authorization status.
- **EEO subgroups:** never take the first option containing a broad word. "Asian" appears in East, South and Southeast Asian. "Indian" appears in "American Indian". The answer bank's `options_hint` is most-specific-first; use it.
- **Long option texts:** "I have not worked at DoorDash", "Yes, I will require Harvey to sponsor my employment", "United States +1". Match by meaning, using `lookup_answer` with the options list.
- **Fill preferred-name fields, pronouns, and GitHub when required**, plus degree/school when the bank says always.
- **Salary dropdowns:** use the bracket that starts at (or contains) the desired number. The `bracket` strategy handles this.
- **Essays:** short, specific, truthful, from `master_resume.json` only. Don't overstate: if the master says "after a major team transition", don't write "when most of the team left". Save essays in `notes.md`.
- **Legal attestations:** the user ticks arbitration agreements and "I certify" boxes (`USER_MUST_TICK`) unless they gave a standing answer.

## Page mechanics
- **Greenhouse (new UI):** questions are react-select comboboxes. Click, type, then click a **visible** `[role=option]`. Hidden lists (like the phone country-code picker) also use `role=option`; restrict to `:visible`.
- **Ashby:** Yes/No questions are buttons inside the field container. Location is an autocomplete: type slowly, wait, and pick the option containing city and state. Field containers may not have the class you expect; find fields by label text.
- **Workday:**
  - Use "Apply Manually".
  - Account creation sends a verification email. The user signs in to Gmail themselves (Google blocks automation with reCAPTCHA). Check that the link's domain is the same `myworkdayjobs.com` tenant before opening it.
  - The Sign In button sits under a `click_filter` layer; click that layer.
  - Date fields are spinbuttons: type `MMYYYY` into Month and it auto-advances.
  - Skills are a type → Enter → click-option multiselect.
  - Dropdowns are `button[aria-haspopup]`.
  - "How did you hear" is nested: Website → "<Company> Careers Website".
  - "Workday is currently unavailable" means a maintenance window; retry later and don't mark the job blocked.
- **Required-field check:** every `*` / `aria-required` field must be filled before "ready". `verify_form` checks this.

## Process
- **Don't touch the page while the user reviews.** They may fix things or submit themselves. Read the page afterwards: if values changed, `record_answer(source="user_filled")`. If a success page shows, record the job as applied with `submitted_by=user` and count the approval.
- **Never say "ready" before `verify_form` returns `pass: true`.**
- **Closed postings happen.** If the page says applications closed, mark the job skipped with the reason.

## 2026-11-07 (Reddit, Greenhouse)
- Bank gaps found on a new form: veteran option "No military service", "select up to 2 ethnicities", demographic-data consent checkbox, "current (or most recent) company". When a new wording maps to an existing answer, record it immediately (source=setup) so the next form is automatic.
- When reading dropdown options, SKIP phone-country and Country lists (200+ entries) — they waste tokens; just type "United States" and pick the visible match.
- DONE (0.2.0): `verify_form` treats decline variants, yes/no prefixes ("No, I do not have..." = "No") and "<Company> careers website" as matches, and `record_answer` + `lookup_answer` match freshly recorded wordings punctuation-insensitively.
- Workday sign-in "wrong email or password / locked" right after account creation = unverified account. Wait for the verification email; retrying locks the account.
- Prefer "Sign in with Google" when offered and the user allows it. Site in maintenance: stop and retry later.

## 2026-11-07 (Frontier, UKG/UltiPro)
- UKG: sign-up = email+password -> name/phone -> straight into the form (no email verification). Resume upload auto-parses work history: ALWAYS re-check Address (parser put the headline in Address 1), current job's end date (parser filled today's month/year), locations, Major, and auto-assigned skill levels (lower secondary skills).
- UKG duplicates element ids (hidden copies): target visible elements only. Date pickers are 3 boxes (mm/dd/yyyy). Document rows each have a type dropdown — set the type on the row matching the FILE NAME, not by index.
