---
name: skills-gap
description: Show which skills the job market is asking for that the user doesn't have yet (demand % across all matched jobs, priority, free learning path, proof project) and turn it into a learning plan. Use when the user asks what to learn, how market-ready they are, or for their skills gap.
---

# Skills gap & learning plan

1. Call `skills_gap_report` (rebuilds `skills_gap.md` and the "Skills Gap" sheet in `tracker.xlsx` from every day's matched jobs + gaps noted while screening).
2. Sanity-check the top gaps: a surprisingly high % can be a loose regex in `config/skills.json` (e.g. the word "go" vs the Go language) or a biased company list: fix the regex and re-run `search_jobs` with `analyze_only=true`.
3. Present: a short table (priority, skill, demand %, why, how to learn, proof project), which gaps to skip (low value for their targets), and a 60–90 day plan ordered by value ÷ effort (quick wins first, then the big one).
4. If a learning path is missing or generic, add a better entry to `config/learning.json` (`"Skill": [why, learn, proof]`).
5. Remind them: a skill moves into `master_resume.json` only after they've genuinely used it (work or a real project): then future resumes pick it up.
