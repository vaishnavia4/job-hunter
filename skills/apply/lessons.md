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
  - Accounts are per company tenant. Auth runs as a state machine: sign in -> (rejected) Create Account -> activation
    email over IMAP -> sign in; or (email taken / no activation email and back at Sign In) Forgot password -> reset
    email -> set the configured password -> sign in. Only open links that are https on the same tenant; never log them.
  - "Sign in with email" first when the Apple/Google/LinkedIn chooser shows. Submit with `signInSubmitButton` (header
    "Sign In" is a nav link). Never fill the hidden `beecatcher` honeypot. The Create Account "I agree" box is the user's.
  - Date fields are spinbuttons: type `MMYYYY` into Month and it auto-advances.
  - Skills are a type -> Enter -> click-option multiselect.
  - Dropdowns are `button[aria-haspopup]`.
  - "How did you hear" is a 2-level multiselect (category -> item, e.g. Website -> "<Company> Careers Website"). Verify the pill.
  - "Workday is currently unavailable" means a maintenance window; retry later and don't mark the job blocked.
- **Required-field check:** every `*` / `aria-required` field must be filled before "ready". `verify_form` checks this.

## Process
- **Don't touch the page while the user reviews.** They may fix things or submit themselves. Read the page afterwards: if values changed, `record_answer(source="user_filled")`. If a success page shows, record the job as applied with `submitted_by=user` and count the approval.
- **Never say "ready" before `verify_form` returns `pass: true`.**
- **Closed postings happen.** If the page says applications closed, mark the job skipped with the reason.

## 2026-11-07 (Reddit, Greenhouse)
- Bank gaps found on a new form: veteran option "No military service", "select up to 2 ethnicities", demographic-data consent checkbox, "current (or most recent) company". When a new wording maps to an existing answer, record it immediately (source=setup) so the next form is automatic.
- When reading dropdown options, SKIP phone-country and Country lists (200+ entries): they waste tokens; just type "United States" and pick the visible match.
- DONE (0.2.0): `verify_form` treats decline variants, yes/no prefixes ("No, I do not have..." = "No") and "<Company> careers website" as matches, and `record_answer` + `lookup_answer` match freshly recorded wordings punctuation-insensitively.
- Workday sign-in "wrong email or password / locked" right after account creation = unverified account. Wait for the verification email; retrying locks the account.
- Prefer "Sign in with Google" when offered and the user allows it. Site in maintenance: stop and retry later.

## 2026-11-07 (Frontier, UKG/UltiPro)
- UKG: sign-up = email+password -> name/phone -> straight into the form (no email verification). Resume upload auto-parses work history: ALWAYS re-check Address (parser put the headline in Address 1), current job's end date (parser filled today's month/year), locations, Major, and auto-assigned skill levels (lower secondary skills).
- UKG duplicates element ids (hidden copies): target visible elements only. Date pickers are 3 boxes (mm/dd/yyyy). Document rows each have a type dropdown: set the type on the row matching the FILE NAME, not by index.
- Always fill OPTIONAL "Links" (LinkedIn, portfolio, GitHub) and "Licenses & Certifications" sections from the profile (earned certs only; dates only if known). The user had to point this out on UKG.
- UKG dialogs (Knockout bindings): use pressSequentially + Tab, not fill(), or the values don't register ("must not be empty").
- VETERAN status: match the EXACT "not a veteran" option ("I AM NOT A VETERAN" / "I am not a protected veteran" / "No military service"). NEVER substring-match "not a protected veteran": it also matches "I IDENTIFY AS A VETERAN, JUST NOT A PROTECTED VETERAN" (happened on STACK, caught on read-back).
- DONE (0.3.0) veteran bug root cause: `choose_option` took the first option matching the hint "not a protected veteran", which is a substring of "I IDENTIFY AS A VETERAN, JUST NOT A PROTECTED VETERAN". Fixed generally: exact/normalized match first, then options that start with the answer, and options that assert the opposite polarity ("I identify as...", "Yes, ...") are removed before any hint or substring match. Same guard covers disability and Hispanic. Degree "Bachelor of Engineering" maps to same-field, then "Bachelor's Degree", then "Bachelor of Science", never "Bachelor of Arts".
- Even so: ALWAYS run the FINAL CHECK (`final_review`) on the Review page. It re-reads every value and the page text; the veteran mistake would have been caught there too.

## 2026-11-07 (Workday, several tenants)
- A new account cannot sign in until the activation link (`/activate/`, valid ~24 h) is opened. "Wrong email or password"
  right after creating the account means "not activated yet", not "wrong password": read the activation mail, then sign
  in once. If Create Account returns to Sign In and no email arrives within ~3 minutes, the account already existed: use
  "Forgot your password?" (reset link contains `/passwordreset/`). Never retry the same password twice in a row.
- School lists contain dozens of "<College>, <University>" entries. Typing the university and clicking the first match
  picked a wrong college once. Scroll until an option EQUALS the school name; if none does, report it.
- Added rows are separate groups ("Websites 1", "Certifications 2"); the Add / Add Another button sits after the section
  heading, not inside the row groups.
- Picking a certification can add an empty required "Specialties N" group that blocks Save: delete it. Workday's cert list
  may lack some certificates: add the ones that exist, delete empty rows, report the rest.
- A later failure in an optional section (websites, certifications) once happened before the resume was uploaded. Upload
  the resume first on My Experience.
- Read back after every step (values, `selectedItem` pills, error messages) and fix empty required fields before Save and Continue.

