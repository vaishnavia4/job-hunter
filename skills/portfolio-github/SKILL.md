---
name: portfolio-github
description: Build or update a professional portfolio website (static, responsive, light/dark, deployed to GitHub Pages with an Actions workflow) and a GitHub profile README from the user's master_resume.json, plus a PUBLIC resume PDF without phone, street address or visa status. Use when the user asks for a portfolio site, GitHub profile, public resume, or to refresh them after the master resume changes.
argument-hint: [output-folder]
---

# Portfolio + GitHub profile

Source of truth is `profile/master_resume.json`. Everything published is **public**, so the rules below are strict.

## Public-content rules
1. **No em dashes or en dashes anywhere** in generated public text (site, README, repo descriptions, PDF). Use commas, colons, hyphens, and " | " in titles. `build_portfolio` rewrites them and reports `dashes_left`; check it is empty.
2. **Public resume PDF**: name, headline, city/state only, email, LinkedIn, website. **No phone, no street address, no visa or work-authorization status.**
3. **No internal company numbers** (revenue, headcount, internal metrics, customer names that are not public) and **no coworker or manager names**. Read every bullet; reword or drop anything that leaks. Ask the user when unsure.
4. **Honest projects only.** Only projects that exist in the master resume or that the user confirms they built. Don't invent demos, stars, users or results. Say what is a learning project.
5. **Pins are set by the user** in the GitHub UI (Profile > Customize your pins). You cannot do it; tell them which repos to pin.
6. Never put credentials or anything from `profile/credentials.json` in the repo.

## Steps
1. Make sure `master_resume.json` is current (new facts get the user's confirmation first). Ask for the GitHub username and whether they want a custom domain (optional).
2. Build: `build_portfolio(out_dir="portfolio")` (MCP) or `python -m jobhunter --workspace . portfolio --out portfolio` (CLI, from the plugin's `server/` folder). It creates:
   - `index.html`, `style.css`, `site.js`: static, no build step, responsive, light/dark (follows the OS, with a toggle). Content comes from `data.json`.
   - `data.json`: the public-safe copy of the master resume. Edit it for polish (add project URLs, a `github` link).
   - `resume.pdf`: the public resume (needs Word on Windows or LibreOffice; otherwise only data is built, say so).
   - `PROFILE_README.md`: draft for the profile repo.
   - `.github/workflows/pages.yml`: deploys the folder to GitHub Pages on push to `main`.
3. Review `data.json` and the PDF against the rules above. Preview locally: `python -m http.server` inside the folder (the page loads `data.json`, so `file://` will not work).
4. Publish (with `gh`, after the user agrees):
   - Portfolio repo: `gh repo create <user>.github.io --public --source portfolio --push` (a user-site repo named `<user>.github.io`), then in Settings > Pages set Source to "GitHub Actions". The workflow deploys automatically.
   - Profile README: create a public repo named exactly `<user>` (same as the username), copy `PROFILE_README.md` to `README.md`, push.
5. Tell the user the live URL, and which 4 to 6 repos to pin in the GitHub UI.
6. Re-run whenever the master resume changes, then commit and push. Keep dashes out of commit messages and repo descriptions too.

## Style
Short, specific copy: what the person does, results with real numbers from the master, links. No buzzword lists, no "passionate about". Titles use " | " (for example "Data Analyst | Finance") or a single plain title.
