#!/usr/bin/env python3
"""Record an LLM Gateway status update, commit it and push it.

Usage examples (run from anywhere; the repo is located relative to this file):

    scripts/update_status.py --status degraded \
        --title "Elevated latency" --message "Upstream provider is slow."
    scripts/update_status.py --status operational \
        --title "Resolved" --message "Latency back to normal."
    scripts/update_status.py --status operational          # heartbeat, no incident entry

What it does, in order:
  1. take a lock, `git pull --rebase` (unless --no-pull / --no-push)
  2. data/status.json    <- current status, updated_at, message
  3. data/history.json   <- today's (UTC) day entry, keeping the worst status of the day
  4. data/incidents.json <- append one event, only when --title is given
  5. git commit (data/ only) with optional --trailer lines, then git push
The push triggers the GitHub Actions workflow that redeploys GitHub Pages.

Python 3 standard library only.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import tempfile

STATUSES = ("operational", "degraded", "down")
RANK = {s: i for i, s in enumerate(STATUSES)}
HISTORY_DAYS = 120          # keep a little more than the 90 shown on the page
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data")


def die(msg, code=1):
    print("update_status: " + msg, file=sys.stderr)
    sys.exit(code)


def git(*args, check=True):
    p = subprocess.run(["git", "-C", REPO, *args], text=True, capture_output=True)
    if check and p.returncode != 0:
        die("git %s failed:\n%s%s" % (" ".join(args), p.stdout, p.stderr))
    return p


def load(name, default):
    path = os.path.join(DATA, name)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        die("%s is not valid JSON: %s" % (path, e))


def save(name, obj):
    path = os.path.join(DATA, name)
    fd, tmp = tempfile.mkstemp(dir=DATA, prefix="." + name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def parse_time(s):
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        die("--time must be ISO 8601, got %r" % s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def apply_update(now, status, title, message):
    day = now.strftime("%Y-%m-%d")

    # status.json
    prev = load("status.json", {})
    msg = message or title
    if not msg and prev.get("status") == status and prev.get("message"):
        msg = prev["message"]  # heartbeat with unchanged status keeps the banner text
    cur = {
        "status": status,
        "updated_at": iso(now),
        "message": msg or "Status reported as %s." % status,
    }
    save("status.json", cur)

    # history.json: worst status of the UTC day wins
    hist = load("history.json", {"days": {}})
    days = hist.setdefault("days", {})
    entry = days.get(day)
    if entry is None or RANK[status] > RANK.get(entry.get("status"), -1):
        entry = {"status": status}
        if title:
            entry["note"] = title
    elif title and RANK[status] == RANK.get(entry.get("status"), -1) and "note" not in entry:
        entry["note"] = title
    days[day] = entry
    keep = sorted(days)[-HISTORY_DAYS:]
    hist["days"] = {k: days[k] for k in keep}
    save("history.json", hist)

    # incidents.json
    event = None
    if title:
        inc = load("incidents.json", {"incidents": []})
        items = inc.setdefault("incidents", [])
        base = now.strftime("%Y%m%dT%H%M%SZ")
        ids = {i.get("id") for i in items}
        n, event_id = 0, base
        while event_id in ids:
            n += 1
            event_id = "%s-%d" % (base, n)
        event = {"id": event_id, "time": iso(now), "status": status, "title": title}
        if message:
            event["message"] = message
        items.append(event)
        save("incidents.json", inc)
    return event


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--status", required=True, choices=STATUSES)
    ap.add_argument("--title", help="short incident/event title; omit for a heartbeat (no incident entry)")
    ap.add_argument("--message", help="longer description shown in the banner and incident log")
    ap.add_argument("--time", help="ISO 8601 timestamp to record (default: now, UTC)")
    ap.add_argument("--trailer", action="append", default=[], metavar="'Key: value'",
                    help="commit trailer line, repeatable (e.g. 'Paperclip-Issue: HOM-503')")
    ap.add_argument("--no-commit", action="store_true", help="only edit the JSON files")
    ap.add_argument("--no-pull", action="store_true", help="skip git pull --rebase")
    ap.add_argument("--no-push", action="store_true", help="commit but do not push")
    ap.add_argument("--dry-run", action="store_true", help="print the new data without writing anything")
    a = ap.parse_args()

    for tr in a.trailer:
        if ":" not in tr or "\n" in tr:
            die("--trailer must be a single 'Key: value' line, got %r" % tr)
    for name, val in (("--title", a.title), ("--message", a.message)):
        if val is not None and not val.strip():
            die("%s must not be empty" % name)

    now = parse_time(a.time) if a.time else dt.datetime.now(dt.timezone.utc)

    if a.dry_run:
        global DATA
        import shutil
        tmpdir = tempfile.mkdtemp(prefix="status-dry-")
        shutil.copytree(DATA, os.path.join(tmpdir, "data"))
        DATA = os.path.join(tmpdir, "data")
        apply_update(now, a.status, a.title, a.message)
        for n in ("status.json", "incidents.json"):
            print("--- %s (dry run)" % n)
            print(open(os.path.join(DATA, n), encoding="utf-8").read())
        shutil.rmtree(tmpdir)
        return

    lock = open(os.path.join(REPO, ".git", "update_status.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        die("another update is in progress", 2)

    do_git = not a.no_commit
    have_remote = do_git and bool(git("remote", check=False).stdout.strip())
    if have_remote and not a.no_pull and not a.no_push:
        git("pull", "--rebase", "--autostash", "--quiet")

    event = apply_update(now, a.status, a.title, a.message)
    print("updated: status=%s at %s%s" % (a.status, iso(now), " (+incident %s)" % event["id"] if event else ""))
    if not do_git:
        return

    git("add", "--", "data")
    if git("diff", "--cached", "--quiet", "--", "data", check=False).returncode == 0:
        print("no changes to commit")
        return
    subject = "status: %s" % a.status + (" - %s" % a.title if a.title else " (heartbeat)")
    body = [subject]
    if a.message:
        body += ["", a.message]
    if a.trailer:
        body += [""] + a.trailer
    git("commit", "--quiet", "-m", "\n".join(body), "--", "data")
    print("committed: " + git("rev-parse", "--short", "HEAD").stdout.strip())

    if a.no_push or not have_remote:
        print("not pushed" + (" (no remote configured)" if not have_remote else ""))
        return
    p = git("push", "--quiet", check=False)
    if p.returncode != 0:  # likely a non-fast-forward; rebase once and retry
        git("pull", "--rebase", "--autostash", "--quiet")
        git("push", "--quiet")
    print("pushed; GitHub Pages will redeploy via Actions")


if __name__ == "__main__":
    main()
