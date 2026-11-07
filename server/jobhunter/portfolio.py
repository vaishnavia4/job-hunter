"""Public portfolio data + public resume PDF, built from profile/master_resume.json.

Public means: no phone, no street address (city/state only), no visa or work-authorization status, and no em or en dashes
in any generated text. Review the output for internal company numbers and coworker names before publishing.
"""
import re
import shutil
from pathlib import Path

from .workspace import TEMPLATES, Workspace, save_json

DASHES = "—–―"  # em dash, en dash, horizontal bar


def clean_text(s):
    """Remove em/en dashes: ' - ' style clause breaks become commas, ranges become hyphens."""
    if not isinstance(s, str):
        return s
    s = re.sub(r"\s*[" + DASHES + r"]\s*", ", ", s) if re.search(r"\s[" + DASHES + r"]\s", s) else re.sub(r"[" + DASHES + r"]", "-", s)
    return s


def _walk(o):
    if isinstance(o, str):
        return clean_text(o)
    if isinstance(o, list):
        return [_walk(x) for x in o]
    if isinstance(o, dict):
        return {k: _walk(v) for k, v in o.items()}
    return o


def city_state(location):
    """'123 Main St, Springfield, IL 62701' -> 'Springfield, IL' (drops street and ZIP)."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    parts = [p for p in parts if not re.match(r"^\d+\s", p)]
    parts = [re.sub(r"\s*\d{5}(-\d{4})?$", "", p) for p in parts]
    return ", ".join(parts[-2:]) if parts else ""


def public_profile(master):
    """Public-safe copy of master_resume.json."""
    c = master.get("contact", {}) or {}
    pub = {
        "name": master.get("name", ""),
        "headline": master.get("headline_default", ""),
        "summary": master.get("summary_default", ""),
        "contact": {"location": city_state(c.get("location", "")), "email": c.get("email", ""),
                    "linkedin": c.get("linkedin", ""), "website": c.get("website", ""), "github": c.get("github", "")},
        "skills": (master.get("skills") or {}).get("primary", {}),
        "experience": [{k: e.get(k, "") for k in ("company", "title", "location", "dates", "bullets")}
                       for e in master.get("experience", [])],
        "projects": master.get("projects", []),
        "certifications": master.get("certifications", []),
        "education": master.get("education", []),
    }
    return _walk(pub)


def find_dashes(obj):
    txt = " ".join(re.findall(r"[^\x00-\x7f]*", str(obj)))
    return [d for d in DASHES if d in txt]


def build_portfolio(ws: Workspace, out_dir, resume_pdf=True):
    out = Path(out_dir)
    if not out.is_absolute():
        out = ws.root / out
    src = TEMPLATES / "portfolio"
    out.mkdir(parents=True, exist_ok=True)
    for f in src.rglob("*"):
        if f.is_file() and f.name != "data.sample.json":
            dst = out / f.relative_to(src)
            if not dst.exists() or f.name in ("index.html", "style.css", "site.js"):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dst)
    pub = public_profile(ws.master())
    save_json(out / "data.json", pub)
    res = {"site": str(out), "data": str(out / "data.json"), "dashes_left": find_dashes(pub)}
    if resume_pdf and pub["name"]:
        try:
            from .resume import build_resume_docx, to_pdf
            r = dict(pub)
            r["skills"] = {k: v for k, v in (pub["skills"] or {}).items()}
            docx = out / "resume.docx"
            build_resume_docx(r, docx)
            pdf, pages, engine = to_pdf(docx)
            docx.unlink(missing_ok=True)
            res.update(resume_pdf=pdf, pages=pages, pdf_engine=engine)
        except Exception as e:
            res["resume_pdf_error"] = f"{type(e).__name__}: {e}"
    # profile README draft for the <username>/<username> repo
    c = pub["contact"]
    lines = [f"# {pub['name']}", "", pub["headline"], ""]
    if pub["summary"]:
        lines += [pub["summary"], ""]
    if pub["skills"]:
        lines += ["## Skills", ""] + [f"- **{k}:** {', '.join(v)}" for k, v in pub["skills"].items()] + [""]
    links = [f"[{n}]({u})" for n, u in (("Portfolio", c.get("website")), ("LinkedIn", c.get("linkedin"))) if u]
    if links:
        lines += ["## Links", "", " | ".join(links), ""]
    (out / "PROFILE_README.md").write_text(clean_text("\n".join(lines)), encoding="utf-8")
    res["profile_readme"] = str(out / "PROFILE_README.md")
    return res
