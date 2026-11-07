"""Command line:  python -m jobhunter <command> [--workspace PATH] ...

  init                         create workspace layout from templates
  extract <resume-file>        print resume text
  search [--limit N] [--analyze-only] [--dry-run] [--exclude-ats a,b] [--preferred-location]
  alerts [--days N] [--dry-run]  read LinkedIn/Jobright alert emails (Gmail App Password)
  add-job --url URL [--title T --company C --location L --description-file F]
  portfolio [--out DIR] [--no-pdf]  build public portfolio data, site files, resume PDF, profile README
  prescreen [--dry-run]        free rule-based skip of 'new' tracker jobs (ITAR, clearance, no sponsorship, off-target titles)
  archive-skipped              move skipped application folders to applications/_skipped
  validate-boards              check which company boards respond
  list [status]                list tracked jobs
  prepare <job_id> | --next N  create application folder(s)
  build <folder>               render tailored resume/cover letter + checks
  set <job_id> key=value ...   update tracker fields (e.g. status=applied)
  sync                         copy decision.json files into the tracker
  skills-gap                   refresh skills_gap.md
  stats                        status counts
  answer "<label>" [--options a|b|c]       look up a form answer
  record "<label>" "<answer>" [--source S] [--hints r1|r2] [--note N]
  verify <fields.json>         verify read-back form fields
  final-check <review.json>    last pre-submit re-verification (fields, files, page text red flags)
  selfcheck                    compile every package module (syntax guard)
"""
import argparse
import json
import sys

from . import alerts, answers, applications, extract, intake, portfolio, resume, search, skills_gap
from .workspace import Workspace, init_workspace


def _p(obj):
    print(obj if isinstance(obj, str) else json.dumps(obj, indent=2, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jobhunter")
    ap.add_argument("--workspace", "-w")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    e = sub.add_parser("extract"); e.add_argument("path")
    s = sub.add_parser("search"); s.add_argument("--limit", type=int); s.add_argument("--analyze-only", action="store_true"); s.add_argument("--dry-run", action="store_true"); s.add_argument("--exclude-ats", default=""); s.add_argument("--preferred-location", action="store_true")
    al = sub.add_parser("alerts"); al.add_argument("--days", type=int); al.add_argument("--dry-run", action="store_true")
    aj = sub.add_parser("add-job"); aj.add_argument("--url", required=True); aj.add_argument("--title", default=""); aj.add_argument("--company", default=""); aj.add_argument("--location", default=""); aj.add_argument("--description-file")
    ps = sub.add_parser("prescreen"); ps.add_argument("--dry-run", action="store_true")
    sub.add_parser("archive-skipped")
    pf = sub.add_parser("portfolio"); pf.add_argument("--out", default="portfolio"); pf.add_argument("--no-pdf", action="store_true")
    sub.add_parser("validate-boards")
    l = sub.add_parser("list"); l.add_argument("status", nargs="?")
    pr = sub.add_parser("prepare"); pr.add_argument("job_id", nargs="?"); pr.add_argument("--next", type=int)
    b = sub.add_parser("build"); b.add_argument("folder")
    st = sub.add_parser("set"); st.add_argument("job_id"); st.add_argument("fields", nargs="*")
    sub.add_parser("sync"); sub.add_parser("skills-gap"); sub.add_parser("stats")
    an = sub.add_parser("answer"); an.add_argument("label"); an.add_argument("--options")
    rc = sub.add_parser("record"); rc.add_argument("label"); rc.add_argument("answer"); rc.add_argument("--source", default="user_correction"); rc.add_argument("--hints"); rc.add_argument("--note", default="")
    vf = sub.add_parser("verify"); vf.add_argument("fields_json")
    fc = sub.add_parser("final-check"); fc.add_argument("review_json")
    sub.add_parser("selfcheck")
    cl = sub.add_parser("cleanup"); cl.add_argument("--dry-run", action="store_true")
    sub.add_parser("export")
    a = ap.parse_args(argv)

    if a.cmd == "init":
        ws, created = init_workspace(a.workspace)
        _p({"workspace": str(ws.root), "created": created})
        return
    if a.cmd == "selfcheck":
        from pathlib import Path
        bad = []
        files = sorted(Path(__file__).resolve().parent.rglob("*.py"))
        for f in files:
            try:
                compile(f.read_text(encoding="utf-8"), str(f), "exec")
            except (SyntaxError, ValueError) as e:
                bad.append(f"{f}: {e}")
        _p({"checked": len(files), "ok": not bad, "errors": bad})
        sys.exit(1 if bad else 0)
    ws = Workspace(a.workspace)
    if a.cmd == "extract":
        print(extract.extract_text(a.path))
    elif a.cmd == "search":
        _p(search.search(ws, limit=a.limit, analyze_only=a.analyze_only, dry_run=a.dry_run, log=lambda m: print(m, file=sys.stderr),
                         exclude_ats=[x.strip() for x in a.exclude_ats.split(',') if x.strip()], preferred_location=a.preferred_location))
    elif a.cmd == "alerts":
        _p(alerts.read_alerts(ws, days=a.days, dry_run=a.dry_run, log=lambda m: print(m, file=sys.stderr)))
    elif a.cmd == "add-job":
        desc = open(a.description_file, encoding="utf-8").read() if a.description_file else ""
        _p(intake.add_job(ws, a.url, a.title, a.company, a.location, desc))
    elif a.cmd == "portfolio":
        _p(portfolio.build_portfolio(ws, a.out, resume_pdf=not a.no_pdf))
    elif a.cmd == "prescreen":
        _p(search.prescreen(ws, dry_run=a.dry_run))
    elif a.cmd == "archive-skipped":
        _p({"moved": intake.archive_skipped(ws)})
    elif a.cmd == "validate-boards":
        _p(search.validate_boards(ws))
    elif a.cmd == "list":
        for r in ws.load_tracker():
            if not a.status or r["status"] == a.status:
                print(f"{r['status']:9} [{r['score']:>3}] {r['company'][:22]:22} | {r['title'][:50]:50} | {r['job_id']}")
    elif a.cmd == "prepare":
        folders = applications.prepare_next(ws, a.next) if a.next else [applications.prepare(ws, a.job_id)]
        for f in folders:
            print(f)
    elif a.cmd == "build":
        _p(resume.build(ws, a.folder))
    elif a.cmd == "set":
        _p(ws.set_fields(a.job_id, **dict(x.split("=", 1) for x in a.fields)))
    elif a.cmd == "sync":
        _p({"synced": applications.sync_decisions(ws)})
    elif a.cmd == "skills-gap":
        _p(skills_gap.run(ws))
    elif a.cmd == "stats":
        _p(ws.stats())
    elif a.cmd == "answer":
        _p(answers.lookup(ws, a.label, a.options.split("|") if a.options else None))
    elif a.cmd == "record":
        _p(answers.record(ws, a.label, a.answer, a.source, a.note, a.hints.split("|") if a.hints else None))
    elif a.cmd == "verify":
        _p(answers.verify_form(ws, json.load(open(a.fields_json, encoding="utf-8"))))
    elif a.cmd == "cleanup":
        from . import cleanup
        _p(cleanup.run(ws, a.dry_run))
    elif a.cmd == "export":
        _p({"xlsx": ws.export_xlsx()})
    elif a.cmd == "final-check":
        res = answers.final_review(ws, json.load(open(a.review_json, encoding="utf-8")))
        _p(res)
        sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main(sys.argv[1:])
