"""PostToolUse hook (Write|Edit): syntax-check the edited file. Non-blocking - failures are reported as a message.

Reads the hook JSON from stdin; *.py -> compile() (same check as py_compile, writes no .pyc), *.js -> node --check. Always exits 0.
"""
import json
import os
import shutil
import subprocess
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path or not os.path.isfile(path):
        return
    err = ""
    if path.endswith(".py"):
        try:
            with open(path, encoding="utf-8") as fh:
                compile(fh.read(), path, "exec")
        except (SyntaxError, ValueError) as e:
            err = str(e)
    elif path.endswith(".js"):
        node = shutil.which("node")
        if node:
            r = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=30)
            if r.returncode:
                err = (r.stderr or r.stdout).strip()
    if err:
        msg = f"SYNTAX ERROR in {path}: {err[:1500]}"
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}, "systemMessage": msg}))


if __name__ == "__main__":
    main()
