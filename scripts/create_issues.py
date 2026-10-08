"""Create GitHub issues for the resume-optimizer project.

Reads scripts/issues.json (UTF-8) and creates milestone (parent) issues first,
then task (child) issues, linking them through the sub-issues API.

Auth: reuses the local GitHub CLI session via `gh auth token`.
All content is read from the UTF-8 JSON file, never from the command line,
to avoid Windows code page corruption of Chinese text.

Usage:
    python create_issues.py --dry-run
    python create_issues.py --only M0
    python create_issues.py
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "create_issues.log")

CANDIDATE_GH = [
    r"C:\Program Files\GitHub CLI\gh.exe",
    "gh",
]


def log(msg):
    line = str(msg)
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_token():
    for exe in CANDIDATE_GH:
        try:
            r = subprocess.run(
                [exe, "auth", "token"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            continue
    return None


def request(method, path, payload, token):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8")
            return True, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        return False, {"status": e.code, "detail": detail[:500]}
    except Exception as e:
        return False, {"status": "exception", "detail": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None, help="only refs starting with this prefix")
    ap.add_argument("--limit", type=int, default=0, help="cap total created issues")
    args = ap.parse_args()

    with open(os.path.join(HERE, "issues.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)

    owner, name = cfg["repo"].split("/", 1)
    issues = cfg["issues"]

    milestones = [i for i in issues if i.get("kind") == "milestone"]
    children = [i for i in issues if i.get("kind") != "milestone"]

    if args.only:
        milestones = [i for i in milestones if i["ref"].startswith(args.only)]
        children = [i for i in children if i["ref"].startswith(args.only)]

    token = None
    if not args.dry_run:
        token = get_token()
        if not token:
            log("FATAL: gh CLI not found or not authenticated")
            return 1

    log("repo=%s/%s  milestones=%d  children=%d  dry_run=%s"
        % (owner, name, len(milestones), len(children), args.dry_run))

    created = {}
    order = milestones + children
    total = args.limit if args.limit else len(order)
    made = 0

    for item in order:
        if made >= total:
            break
        ref = item["ref"]
        parent = item.get("parent")

        if args.dry_run:
            log("[dry] %s | %s" % (ref, item["title"]))
            created[ref] = {"number": 0, "id": 0}
            made += 1
            continue

        ok, res = request("POST", "/repos/%s/%s/issues" % (owner, name),
                          {"title": item["title"], "body": item.get("body", "")}, token)
        if not ok:
            log("FAIL  %s | %s" % (ref, res))
            continue

        num, iid = res.get("number"), res.get("id")
        created[ref] = {"number": num, "id": iid}
        log("OK    %s -> #%s" % (ref, num))
        made += 1

        if parent and parent in created and created[parent]["number"]:
            pnum = created[parent]["number"]
            ok2, res2 = request(
                "POST",
                "/repos/%s/%s/issues/%d/sub_issues" % (owner, name, pnum),
                {"sub_issue_id": iid}, token,
            )
            log("      link %s under #%s : %s" % (ref, pnum, "ok" if ok2 else res2))

        time.sleep(0.6)

    log("done. created=%d" % made)
    return 0


if __name__ == "__main__":
    sys.exit(main())
