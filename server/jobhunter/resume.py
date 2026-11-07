"""ATS-friendly resume / cover letter rendering, truth check and keyword match report.

An application folder contains:
  job.json          saved posting (from prepare_application)
  tailored.json     same schema as profile/master_resume.json minus extras:
                    name, contact{location,phone,email,linkedin,work_auth}, headline, summary,
                    skills{category:[...]}, experience[{company,title,location,dates,bullets}],
                    certifications[], education[{degree,school}]
  cover_letter.md   optional, plain paragraphs
Produces Resume_<First>_<Last>_<Company>.docx/.pdf, CoverLetter_... and match_report.md
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .search import load_vocab
from .workspace import Workspace, load_json, save_json, slug

FONT = "Calibri"


def _docx():
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
    return Document, WD_ALIGN_PARAGRAPH, OxmlElement, qn, Inches, Pt, RGBColor


def _base_doc():
    Document, _, _, qn, Inches, Pt, _ = _docx()
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.left_margin = sec.right_margin = Inches(0.6)
    sec.top_margin = sec.bottom_margin = Inches(0.5)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = FONT, Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    st.paragraph_format.space_after = Pt(0)
    st.paragraph_format.space_before = Pt(0)
    return doc


def _rule(p):
    _, _, OxmlElement, qn, _, _, _ = _docx()
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "1F3A5F")):
        b.set(qn(k), v)
    bdr.append(b)
    pPr.append(bdr)


def _rich(p, text):
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if part:
            p.add_run(part).bold = bool(i % 2)


def _heading(doc, text):
    *_, Pt, RGBColor = _docx()
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(7)
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(text.upper())
    r.bold, r.font.size, r.font.color.rgb = True, Pt(11), RGBColor(0x1F, 0x3A, 0x5F)
    _rule(p)


def _bullet(doc, text):
    _, _, _, _, Inches, Pt, _ = _docx()
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.left_indent = Inches(0.22)
    p.paragraph_format.space_after = Pt(1.5)
    _rich(p, text)


def _tab_line(doc, left, right, italic=False):
    _, _, _, _, Inches, Pt, _ = _docx()
    p = doc.add_paragraph()
    p.paragraph_format.tab_stops.add_tab_stop(Inches(7.3), alignment=2)
    a = p.add_run(left)
    a.bold, a.italic = True, italic
    b = p.add_run("\t" + (right or ""))
    b.italic = italic
    return p


def build_resume_docx(r, out_docx):
    _, WD_ALIGN_PARAGRAPH, _, _, _, Pt, RGBColor = _docx()
    doc = _base_doc()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    n = p.add_run(r["name"])
    n.bold, n.font.size, n.font.color.rgb = True, Pt(20), RGBColor(0x1F, 0x3A, 0x5F)
    if r.get("headline"):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(r["headline"]).bold = True
    c = r.get("contact", {})
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run("  |  ".join(filter(None, [c.get(k) for k in ("location", "phone", "email", "linkedin", "website", "work_auth")]))).font.size = Pt(9.5)
    if r.get("summary"):
        _heading(doc, "Professional Summary")
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        _rich(p, r["summary"])
    if r.get("skills"):
        _heading(doc, "Core Skills")
        for cat, items in r["skills"].items():
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(1)
            p.add_run(f"{cat}: ").bold = True
            p.add_run(", ".join(items))
    _heading(doc, "Professional Experience")
    for i, e in enumerate(r.get("experience", [])):
        p = _tab_line(doc, e["company"], e.get("dates", ""))
        if i:
            p.paragraph_format.space_before = Pt(5)
        _tab_line(doc, e["title"], e.get("location", ""), italic=True)
        for b in e.get("bullets", []):
            _bullet(doc, b)
    for sec_name, key in (("Projects", "projects"),):
        if r.get(key):
            _heading(doc, sec_name)
            for pr in r[key]:
                _bullet(doc, pr if isinstance(pr, str) else f"**{pr.get('name', '')}**: {pr.get('description', '')}")
    if r.get("certifications"):
        _heading(doc, "Certifications")
        for cert in r["certifications"]:
            _bullet(doc, cert)
    if r.get("education"):
        _heading(doc, "Education")
        for ed in r["education"]:
            _tab_line(doc, ed.get("degree", ""), ed.get("school", ""))
    doc.save(out_docx)


def build_cover_letter_docx(md_text, profile, out_docx):
    _, _, _, _, Inches, Pt, RGBColor = _docx()
    doc = _base_doc()
    s = doc.sections[0]
    s.left_margin = s.right_margin = Inches(0.9)
    s.top_margin = Inches(0.8)
    pp = profile.get("personal", {})
    p = doc.add_paragraph()
    n = p.add_run(pp.get("full_name", ""))
    n.bold, n.font.size, n.font.color.rgb = True, Pt(16), RGBColor(0x1F, 0x3A, 0x5F)
    doc.add_paragraph("  |  ".join(filter(None, [f"{pp.get('city', '')}, {pp.get('state_code', '')}".strip(", "),
                                                  pp.get("phone"), pp.get("email"),
                                                  (pp.get("linkedin") or "").replace("https://www.", "")])))
    _rule(doc.paragraphs[-1])
    for para in [x.strip() for x in md_text.split("\n\n") if x.strip()]:
        q = doc.add_paragraph(para.replace("\n", " "))
        q.paragraph_format.space_before = Pt(8)
        q.paragraph_format.line_spacing = 1.1
    doc.save(out_docx)


# ---------------------------------------------------------------- PDF
def to_pdf(docx_path):
    """Word (Windows COM) -> LibreOffice -> None. Returns (pdf_path|None, pages|None, engine)."""
    docx_path = Path(docx_path).resolve()
    pdf = docx_path.with_suffix(".pdf")
    if sys.platform == "win32":
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            try:
                d = word.Documents.Open(str(docx_path), ReadOnly=True)
                d.SaveAs(str(pdf), FileFormat=17)
                pages = d.ComputeStatistics(2)
                d.Close(False)
                return str(pdf), pages, "word"
            finally:
                word.Quit()
        except Exception:
            pass
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    for cand in (r"C:\Program Files\LibreOffice\program\soffice.exe", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if not soffice and Path(cand).exists():
            soffice = cand
    if soffice:
        try:
            subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(docx_path.parent), str(docx_path)],
                           check=True, capture_output=True, timeout=180)
            return str(pdf), _pdf_pages(pdf), "libreoffice"
        except Exception:
            pass
    return None, None, "none"


def _pdf_pages(pdf):
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf)).pages)
    except Exception:
        return None


# ---------------------------------------------------------------- checks
def _flatten(r):
    parts = [r.get("headline", ""), r.get("summary", "")]
    for k, v in (r.get("skills") or {}).items():
        parts += [k] + list(v)
    for e in r.get("experience", []):
        parts += [e.get("title", "")] + e.get("bullets", [])
    parts += r.get("certifications", [])
    return "\n".join(parts)


# ---------------------------------------------------------------- tailoring levels
# low    : headline, summary, skill order + at most 4 reworded/swapped bullets (easy for the user to own)
# medium : up to 12 bullets reworded/reordered with JD keywords (default)
# high   : every bullet may be rewritten around the JD; facts, numbers and skills must still come from the master
LEVELS = {"auto": None, "low": 4, "medium": 12, "high": None}
LEVEL_GUIDE = {
    "auto": "Standard tailoring (the workspace's normal rules in CLAUDE.md / the tailor-resume skill); no extra budget. "
            "Used for automated batches; low/medium/high apply only when the user names a level for a job.",
    "low": "Change only the headline, summary and skill order, plus at most 4 bullets (swap in a more relevant master bullet "
           "or lightly reword with JD keywords). Keep every other bullet word-for-word from the master resume.",
    "medium": "Reorder and reword up to 12 bullets with the JD's vocabulary, drop irrelevant skill categories; keep each "
              "bullet's facts and structure.",
    "high": "Rewrite everything around the JD: every bullet reworded and ranked by JD relevance, summary mirroring the JD's "
            "top requirements, projects chosen for the JD. Still ONLY facts, numbers, tools and skills from the master resume.",
}


def tailor_level(ws, folder, override=None):
    """Level for a folder: explicit override > job.json 'tailor_level' > profile tailoring.default_level > auto."""
    if override:
        lvl = override
    else:
        job = load_json(Path(folder) / "job.json", {}) or {}
        lvl = job.get("tailor_level") or ((ws.profile() or {}).get("tailoring") or {}).get("default_level") or "auto"
    lvl = str(lvl).lower()
    if lvl not in LEVELS:
        raise ValueError(f"tailor level must be one of {list(LEVELS)}, got {lvl!r}")
    return lvl


def set_tailor_level(folder, level):
    p = Path(folder) / "job.json"
    job = load_json(p, {}) or {}
    job["tailor_level"] = level
    save_json(p, job)


def _plain(s):
    return re.sub(r"\s+", " ", re.sub(r"\*\*", "", s or "")).strip()


def change_report(tailored, master):
    """Compare each tailored bullet with the closest master bullet of the same employer.
    Returns {"roles": [...], "reworded": n, "kept": n, "dropped": n, "headline_changed": bool, "summary_changed": bool}."""
    from difflib import SequenceMatcher
    roles, rew, kept, dropped = [], 0, 0, 0
    m_by = {e.get("company"): e for e in master.get("experience", [])}
    for t in tailored.get("experience", []):
        mb = [_plain(b) for b in (m_by.get(t.get("company")) or {}).get("bullets", [])]
        used, items = set(), []
        for b in t.get("bullets", []):
            pb = _plain(b)
            best, score = None, 0.0
            for i, m in enumerate(mb):
                s = SequenceMatcher(None, pb.lower(), m.lower()).ratio()
                if s > score:
                    best, score = i, s
            if best is not None:
                used.add(best)
            status = "kept" if score >= 0.97 else "reworded"
            kept += status == "kept"
            rew += status == "reworded"
            items.append({"status": status, "tailored": pb, "closest_master": mb[best] if best is not None else "",
                          "similarity": round(score, 2)})
        drop = [m for i, m in enumerate(mb) if i not in used]
        dropped += len(drop)
        roles.append({"company": t.get("company"), "bullets": items, "dropped": drop})
    return {"roles": roles, "reworded": rew, "kept": kept, "dropped": dropped,
            "headline_changed": _plain(tailored.get("headline")) != _plain(master.get("headline")),
            "summary_changed": _plain(tailored.get("summary")) != _plain(master.get("summary"))}


def changes_markdown(rep, level, company=""):
    budget = LEVELS[level]
    lines = [f"# Tailoring changes{(' - ' + company) if company else ''}", "",
             f"**Level: {level}** ({LEVEL_GUIDE[level]})", "",
             f"Bullets kept word-for-word: **{rep['kept']}** | reworded or swapped: **{rep['reworded']}**"
             + (f" (budget {budget})" if budget is not None else " (no limit)") + f" | master bullets not used: {rep['dropped']}",
             f"Headline changed: {'yes' if rep['headline_changed'] else 'no'} | summary changed: {'yes' if rep['summary_changed'] else 'no'}",
             "", "Be ready to talk about every reworded line: each one must still describe something you really did.", ""]
    for r in rep["roles"]:
        lines += [f"## {r['company']}", ""]
        for b in r["bullets"]:
            if b["status"] == "kept":
                lines.append(f"- KEPT: {b['tailored']}")
            else:
                lines += [f"- REWORDED ({int(b['similarity'] * 100)}% similar):", f"  - now: {b['tailored']}",
                          f"  - from: {b['closest_master']}"]
        if r["dropped"]:
            lines += ["", "Not used for this job:"] + [f"- {d}" for d in r["dropped"]]
        lines.append("")
    return "\n".join(lines)


_NUM = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|[kKmMbB]\b|\+)?")


def _numbers(text):
    out = set()
    text = re.sub(r"\b0[- ]to[- ]1\b|\b24\s*[x×]\s*7\b|\bTLS\s*1\.\d\b|\bv?\d+\.\d+\.\d+\b", " ", _plain(text), flags=re.I)  # phrases, not metrics
    for n in _NUM.findall(text):
        core = re.sub(r"[\s,]", "", n).lower().rstrip("+")
        if core and not re.fullmatch(r"(19|20)\d\d", core):  # years are checked via dates
            out.add(core)
    return out


def truth_check(tailored, master):
    """Skills/certs must exist in master; employers/titles/dates must match; headline must be one title;
    every number in the summary and bullets must appear in the master (guards heavy rewording)."""
    master_text = json.dumps(master).lower()
    issues = []
    m_nums = _numbers(json.dumps(master, ensure_ascii=False))
    t_text = " ".join([tailored.get("summary", "")] + [b for e in tailored.get("experience", []) for b in e.get("bullets", [])])
    for n in sorted(_numbers(t_text) - m_nums):
        issues.append(f"Number not in master resume: '{n}' (metrics must be exact)")
    for cat, items in (tailored.get("skills") or {}).items():
        for s in items:
            core = re.sub(r"\(.*?\)", "", s).strip().lower()
            if core and core not in master_text and s.lower() not in master_text:
                issues.append(f"Skill not in master resume: '{s}' ({cat})")
    for cert in tailored.get("certifications", []):
        if cert.split(" - ")[0].lower()[:40] not in master_text:
            issues.append(f"Certification not in master resume: '{cert}'")
    t_exp, m_exp = tailored.get("experience", []), master.get("experience", [])
    if [e["company"] for e in t_exp] != [e["company"] for e in m_exp]:
        issues.append("Employers/order differ from master resume")
    for t, m in zip(t_exp, m_exp):
        if t.get("title") != m.get("title") or t.get("dates") != m.get("dates"):
            issues.append(f"Title/dates changed for {m.get('company')}")
    if "|" in (tailored.get("headline") or ""):
        issues.append("Headline should be ONE job title (no '|' lists)")
    return issues


def match_report(ws, job, tailored, master):
    vocab = load_vocab(ws)
    jd = job.get("title", "") + "\n" + job.get("description", "")
    res_txt, master_txt = _flatten(tailored), json.dumps(master)
    found = [k for k, (rx, _) in vocab.items() if rx.search(jd)]
    covered = [k for k in found if vocab[k][0].search(res_txt)]
    could_add = [k for k in found if k not in covered and vocab[k][0].search(master_txt)]
    gaps = [k for k in found if k not in covered and k not in could_add]
    return len(covered) / max(1, len(found)), covered, could_add, gaps


# ---------------------------------------------------------------- main entry
def build(ws: Workspace, folder, level=None):
    folder = Path(folder)
    if not folder.is_absolute():
        folder = ws.root / folder
    if level:
        set_tailor_level(folder, tailor_level(ws, folder, level))
    lvl = tailor_level(ws, folder)
    profile, master = ws.profile(), ws.master()
    tailored = load_json(folder / "tailored.json")
    if not tailored:
        raise FileNotFoundError(f"{folder / 'tailored.json'} not found")
    job = load_json(folder / "job.json", {})
    pp = profile.get("personal", {})
    company = slug(job.get("company", folder.name.split("_")[0]), 30)
    base = f"{slug(pp.get('first_name', 'Resume'))}_{slug(pp.get('last_name', ''))}_{company}".replace("__", "_")

    issues = truth_check(tailored, master)
    resume_docx = folder / f"Resume_{base}.docx"
    build_resume_docx(tailored, resume_docx)
    pdf, pages, engine = to_pdf(resume_docx)
    if pages and pages > 2:
        issues.append(f"Resume is {pages} pages - trim to 2")
    out = {"resume_docx": str(resume_docx), "resume_pdf": pdf, "pages": pages, "pdf_engine": engine}
    cl = folder / "cover_letter.md"
    if cl.exists():
        cl_docx = folder / f"CoverLetter_{base}.docx"
        build_cover_letter_docx(cl.read_text(encoding="utf-8"), profile, cl_docx)
        out["cover_letter_docx"] = str(cl_docx)
        out["cover_letter_pdf"] = to_pdf(cl_docx)[0]
        if re.search(r"\b(although I (don't|do not|lack)|I (don't|do not) have|my gap|lack(ing)? experience)", cl.read_text(encoding="utf-8"), re.I):
            issues.append("Cover letter seems to mention a gap - remove it")
    pct, covered, could_add, gaps = match_report(ws, job, tailored, master)
    rep = [f"# Match report - {job.get('company')} - {job.get('title')}", "",
           f"**JD keyword coverage: {pct:.0%}** ({len(covered)} of {len(covered) + len(could_add) + len(gaps)} JD skill areas on the resume)", "",
           f"**Covered:** {', '.join(covered) or '-'}", "",
           f"**In your background but not on this resume (consider adding):** {', '.join(could_add) or 'none'}", "",
           f"**True gaps (not in master resume - NOT added):** {', '.join(gaps) or 'none'}", "",
           f"**Truth check:** {'PASS' if not issues else 'REVIEW'}"] + [f"- {i}" for i in issues]
    (folder / "match_report.md").write_text("\n".join(rep), encoding="utf-8")
    ch = change_report(tailored, master)
    (folder / "changes.md").write_text(changes_markdown(ch, lvl, job.get("company", "")), encoding="utf-8")
    budget = LEVELS[lvl]
    level_note = (f"{ch['reworded']} bullets reworded, over the '{lvl}' budget of {budget}: keep more master bullets word-for-word"
                  if budget is not None and ch["reworded"] > budget else "ok")
    out.update({"coverage": f"{pct:.0%}", "gaps": gaps, "could_add": could_add,
                "truth_check": "PASS" if not issues else issues,
                "tailor_level": lvl, "bullets_reworded": ch["reworded"], "bullets_kept": ch["kept"], "level_check": level_note})
    return out
