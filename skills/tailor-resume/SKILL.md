---
name: tailor-resume
description: Tailor a truthful, ATS-friendly 2-page resume and cover letter for one job in a job-hunter application folder - mirrors the job description's keywords only where the master resume backs them, single-title headline, no gaps in cover letters, then builds DOCX/PDF and runs the truth check. Use when asked to tailor/customize a resume for a job, or as the tailoring step of job-batch.
argument-hint: [application-folder]
---

# Tailor a resume (truthfully)

Folder = $ARGUMENTS (an `applications/<Company>_<Role>/` folder created by `prepare_application`).

1. Read `JD.md`, `profile/master_resume.json`, `profile/profile.json`, `config/skills.json`.
2. **Screen first** if no `decision.json` exists: honest fit % and gaps. Skip (write `{"decision":"skip","reason":...}`) when the JD requires citizenship/clearance the user lacks, says no sponsorship and they need it, needs a core skill they don't have (main programming language, specialist domain), is far more senior, pays below the floor, or is really a different function (sales/presales when they're an analyst, etc.).
3. Write `tailored.json` (same schema as the master: name, contact, headline, summary, skills{category:[...]}, experience[...], certifications, education, optional projects):
   - **Headline = ONE job title** — the posting's title, cleaned (e.g. "Senior Business Analyst"). No pipes or skill lists.
   - **Summary**: 3–4 sentences mirroring the JD's top requirements in its own vocabulary, only where true; 2–3 of the strongest metrics in **bold**.
   - **Skills**: reorder categories/items so JD keywords lead; use the JD's spelling ("CI/CD", "BRD", "UAT"); drop irrelevant categories. Secondary skills only if the JD asks.
   - **Experience**: same employers, titles, dates, order as the master. Pick and reword the most relevant bullets (current role ~7–10, older roles 3–5). Keep every number exact. Bold 1–2 metrics per role with `**...**`.
   - **Never** add a skill, tool, metric, responsibility or cert that isn't in the master. If a JD keyword is missing, it's a gap — not a resume line.
4. Write `cover_letter.md`: 3–4 short paragraphs (~220–280 words) specific to this company/team, one or two concrete metrics, why this company, no clichés, **no mention of gaps or missing skills**, signed with the user's full name.
5. Call `build_resume` (folder). Must report 2 pages and `truth_check: PASS`. If 3 pages: trim the least relevant bullets/skills. If the truth check flags a skill: use the master's exact wording or remove it. Rebuild until clean.
6. Update `notes.md` ("Why this role fits": fit %, gaps, location, salary; "Tailoring changes") and `decision.json` (`{"decision":"apply","fit":N,"coverage":"x%","gaps":[...],"notes":"relocation/on-site/expected questions"}`), then `sync_decisions`.
7. Before any upload, open the built PDF's first lines (or re-run build) and confirm the headline is the single title — a stale PDF is the most common mistake.
