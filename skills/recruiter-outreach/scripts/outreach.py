#!/usr/bin/env python3
"""outreach.py : bookkeeping CLI for skills/recruiter-outreach.

It never contacts LinkedIn. The assistant acts in the browser and records each step here; the
commands refuse a step that breaks the user's rules. A send is always:

  outreach.py claim --id N        # every check runs, the row is reserved ('sending')
  ... the click in the browser ...
  outreach.py mark-sent --id N    # or mark-failed when the page shows it did not go out

Results are JSON on stdout; a refusal is a message on stderr and exit code 2. Dates are always
today's local date: there is no override, so limits and expiry cannot be bypassed.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from lib import config_loader, jobdb, outreachdb as od  # noqa: E402
from lib.paths import CONFIG, DB_PATH, ROOT as AGENT_ROOT  # noqa: E402


def _guard():
    if not (CONFIG / "outreach.md").is_file():
        raise od.OutreachError(f"{CONFIG / 'outreach.md'} not found; copy "
                               f"config/outreach.example.md and fill it in")
    try:
        DB_PATH.resolve().relative_to(AGENT_ROOT.resolve())
    except ValueError:
        return
    raise od.OutreachError("the tracker resolves inside the JobAgent folder; set "
                           "JOBAGENT_DATA_DIR to your data folder")


def _read(path):
    return Path(path).read_text(encoding="utf-8").strip() if path else ""


def _table(rows):
    lines = ["| id | person | company | kind | chars | text | attachment |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        text = (r["body"] or "(no note)").replace("\n", " / ").replace("|", "\\|")
        lines.append(f"| {r['id']} | {r['name']}, {r['title']} | {r['company']} | {r['kind']} | "
                     f"{len(r['body'] or '')} | {text} | {Path(r['attachment_path']).name if r['attachment_path'] else ''} |")
    return "\n".join(lines)


def run(a, con, cfg, today):
    cmd = a.cmd
    if cmd == "add-contact":
        cid = od.add_contact(con, company=a.company, name=a.name, title=a.title,
                             profile_url=a.profile_url, group=a.group, cfg=cfg, job_id=a.job_id,
                             location=a.location, hook=a.hook, sponsor_evidence=a.sponsor_evidence)
        return {"id": cid}
    if cmd == "verify":
        od.verify_contact(con, a.id, today)
        return {"id": a.id, "status": "ready"}
    if cmd == "set-status":
        od.set_contact_status(con, a.id, a.status, reason=a.reason,
                              next_action_date=a.next_action_date)
        return {"id": a.id, "status": a.status}
    if cmd == "set-thread":
        od.set_thread_url(con, a.id, a.url)
        return {"id": a.id}
    if cmd == "draft":
        mid = od.draft(con, a.contact, a.kind, _read(a.body_file), cfg, today,
                       attachment_path=a.attachment)
        return {"id": mid}
    if cmd == "edit":
        od.edit(con, a.id, _read(a.body_file), cfg)
        return {"id": a.id, "status": "draft"}
    if cmd == "requeue":
        od.requeue(con, a.id, cfg, today)
        return {"id": a.id, "status": "draft"}
    if cmd == "batch":
        token, rows = od.make_batch(con, today)
        if not rows:
            return "nothing to show"
        return f"token: {token}\n\n{_table(rows)}"
    if cmd == "approve":
        ids = [int(x) for x in a.ids.split(",") if x.strip()]
        od.approve(con, a.token, ids, today)
        return {"approved": ids}
    if cmd == "claim":
        return od.claim(con, a.id, cfg, today)
    if cmd == "mark-sent":
        od.mark_sent(con, a.id, today, cfg)
        return {"id": a.id, "status": "sent"}
    if cmd == "mark-failed":
        od.mark_failed(con, a.id)
        return {"id": a.id, "status": "failed"}
    if cmd == "log-in":
        mid = od.log_in(con, a.contact, today, summary=a.summary, accepted=a.accepted)
        return {"id": mid}
    if cmd == "due":
        return od.due(con, cfg, today)
    if cmd == "pause":
        until = od.pause(con, today, a.days, cfg)
        return {"paused_until": until.isoformat()}
    if cmd == "resume":
        od.resume(con)
        return {"paused_until": None}
    if cmd == "status":
        od.ensure_schema(con)
        by = dict(con.execute("SELECT status, COUNT(*) FROM contacts GROUP BY status").fetchall())
        t = today.isoformat()
        return {"contacts": by,
                "today_sending": od._count_out(con, t, t) - con.execute(
                    "SELECT COUNT(*) FROM outreach_messages WHERE status='sent' AND sent_on=?",
                    (t,)).fetchone()[0],
                "today_sent": con.execute(
                    "SELECT COUNT(*) FROM outreach_messages WHERE status='sent' AND sent_on=? "
                    "AND direction='out'", (t,)).fetchone()[0],
                "paused_until": od.paused_until(con)}
    raise od.OutreachError(f"unknown command {cmd}")


def parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add-contact")
    for f in ("--company", "--name", "--title", "--profile-url"):
        s.add_argument(f, required=True)
    s.add_argument("--group", required=True, choices=["applied", "target"])
    s.add_argument("--job-id", type=int)
    s.add_argument("--location")
    s.add_argument("--hook")
    s.add_argument("--sponsor-evidence")
    for name in ("verify", "requeue", "claim", "mark-sent", "mark-failed"):
        sub.add_parser(name).add_argument("--id", type=int, required=True)
    s = sub.add_parser("set-status")
    s.add_argument("--id", type=int, required=True)
    s.add_argument("--status", required=True, choices=["closed"])
    s.add_argument("--reason", choices=od.CLOSE_REASONS)
    s.add_argument("--next-action-date")
    s = sub.add_parser("set-thread")
    s.add_argument("--id", type=int, required=True)
    s.add_argument("--url", required=True)
    s = sub.add_parser("draft")
    s.add_argument("--contact", type=int, required=True)
    s.add_argument("--kind", required=True, choices=od.MESSAGE_KINDS)
    s.add_argument("--body-file")
    s.add_argument("--attachment")
    s = sub.add_parser("edit")
    s.add_argument("--id", type=int, required=True)
    s.add_argument("--body-file", required=True)
    sub.add_parser("batch")
    s = sub.add_parser("approve")
    s.add_argument("--token", required=True)
    s.add_argument("--ids", required=True)
    s = sub.add_parser("log-in")
    s.add_argument("--contact", type=int, required=True)
    s.add_argument("--summary")
    s.add_argument("--accepted", action="store_true")
    sub.add_parser("due")
    s = sub.add_parser("pause")
    s.add_argument("--days", type=int)
    sub.add_parser("resume")
    sub.add_parser("status")
    return p


def main(argv=None):
    # Names in Brazil and elsewhere are not ASCII; the Windows console codepage would crash.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    a = parser().parse_args(argv)
    try:
        _guard()
        cfg = config_loader.outreach()
        con = jobdb.connect()
        out = run(a, con, cfg, datetime.date.today())
    except od.OutreachError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
