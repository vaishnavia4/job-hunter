"""Housekeeping: delete transient files that pile up (browser snapshots, previews, old search dumps).

Never touches the tracker, profile, application folders or config. Extra rules can be added in
config/cleanup.json: {"rules": [{"dir": "data", "globs": ["tmp_*.png"], "days": 0}]}
"""
import time

DEFAULT_RULES = [
    {"dir": ".playwright-mcp", "globs": ["*.yml", "*.log", "*.png", "*.jpeg", "*.jpg"], "days": 3},
    {"dir": "data", "globs": ["preview_*.png", "*_signin.jpg", "*_mid.png", "*_tmp.png"], "days": 0},
    {"dir": "jobs", "globs": ["*_all_matches.json"], "days": 14},
]
PROTECTED = {"profile", "config", "applications"}


def run(ws, dry_run=False):
    rules = DEFAULT_RULES + list((ws.cfg("cleanup", {}) or {}).get("rules", []))
    now, freed, n = time.time(), 0, 0
    for r in rules:
        folder = (ws.root / r["dir"]).resolve()
        if r["dir"].split("/")[0] in PROTECTED or ws.root not in folder.parents and folder != ws.root:
            continue
        if not folder.exists():
            continue
        for g in r["globs"]:
            for f in folder.glob(g):
                if f.is_file() and now - f.stat().st_mtime >= r.get("days", 0) * 86400:
                    freed += f.stat().st_size
                    n += 1
                    if not dry_run:
                        f.unlink()
    return {"deleted" if not dry_run else "would_delete": n, "mb": round(freed / 1e6, 1)}
