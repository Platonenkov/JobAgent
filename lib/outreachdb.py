"""Bookkeeping for recruiter outreach on LinkedIn (skills/recruiter-outreach).

The tables live in jobs.db next to `jobs`, which this module never alters. Nothing here talks to
LinkedIn: the assistant sends in the browser and records each step, and this module refuses a
step that breaks the user's rules. Every send is claim -> browser -> mark_sent / mark_failed, so
the checks run before the click; a row left in `sending` is never offered for sending again.

Approval is bound to what the user saw: make_batch stores a hash of the exact action (recipient,
kind, body, attachment bytes) and a batch token; approve accepts only rows of that batch whose
hash is unchanged, and claim re-checks the hash.

Errors name message and contact ids and companies only, never a person or a message body.
"""
import datetime
import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import jobdb  # noqa: E402


class OutreachError(Exception):
    pass


CONTACT_STATUSES = ["found", "ready", "invited", "connected", "talking", "cv_sent", "closed"]
CLOSE_REASONS = ["no_reply", "declined", "left_company", "not_relevant", "owner_skip",
                 "invite_ignored"]
MESSAGE_KINDS = ["invite", "invite_note", "message", "follow_up", "cv"]
MESSAGE_STATUSES = ["draft", "shown", "approved", "sending", "sent", "failed", "received"]
INVITE_KINDS = ("invite", "invite_note")
CONVERSATION = ("connected", "talking", "cv_sent")

DEFAULTS = {
    "daily_new_invites": 5, "weekly_invites": 80, "max_outgoing_per_day": 10,
    "max_per_company": 2, "follow_up_days": 7, "approval_valid_days": 1, "pause_days": 7,
    "note_max_chars": 200, "verify_max_age_days": 14, "sponsor_required_markets": ["us"],
    "target_companies": [], "exclude_companies": [], "us_sponsors": [],
}

# One statement per execute: executescript would commit a transaction the caller has open.
_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS contacts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      company TEXT NOT NULL, company_canon TEXT NOT NULL,
      name TEXT, title TEXT, profile_url TEXT UNIQUE NOT NULL, location TEXT, market TEXT,
      grp TEXT NOT NULL, job_id INTEGER, hook TEXT, sponsor_evidence TEXT, thread_url TEXT,
      status TEXT NOT NULL, close_reason TEXT, verified_on TEXT, next_action_date TEXT,
      notes TEXT, created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS outreach_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      contact_id INTEGER NOT NULL REFERENCES contacts(id),
      direction TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
      attachment_path TEXT, attachment_sha256 TEXT, status TEXT NOT NULL,
      shown_hash TEXT, batch_token TEXT, approved_hash TEXT, approved_on TEXT, sent_on TEXT,
      created_at TEXT)""",
    "CREATE TABLE IF NOT EXISTS outreach_state (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_contacts_canon ON contacts(company_canon)",
    "CREATE INDEX IF NOT EXISTS idx_om_contact ON outreach_messages(contact_id)",
]


def ensure_schema(con):
    for stmt in _SCHEMA:
        con.execute(stmt)


def settings(cfg):
    out = dict(DEFAULTS)
    out.update(cfg or {})
    return out


def _d(day):
    return day.isoformat()


def _plus(day, days):
    return _d(day + datetime.timedelta(days=int(days)))


# Names and URLs

_EXTRA_ENTITY = re.compile(r"\b(corp|corporation|co|company|services)\b")


def canon_company(name):
    s = (name or "").lower().replace(".com", " ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = jobdb._ENTITY.sub(" ", s)
    s = _EXTRA_ENTITY.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_profile_url(url):
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        raise OutreachError("not a LinkedIn URL")
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 2 or segs[0].lower() != "in":
        raise OutreachError("not a LinkedIn profile URL (/in/<name>)")
    slug = unquote(segs[1]).lower()
    if not slug or slug.startswith("acoaa"):
        raise OutreachError("profile URL has no public name; open the profile and copy its URL")
    return f"https://www.linkedin.com/in/{slug}"


def _listed(canon, names):
    return any(canon == canon_company(n) for n in names or [])


# Contacts

def _contact(con, cid):
    row = con.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
    if row is None:
        raise OutreachError(f"contact {cid} not found")
    return row


def _company_count(con, canon):
    """Lifetime count for the per-company cap: everyone not closed, plus anyone closed after
    a message actually went out (closing a conversation does not free a slot)."""
    return con.execute(
        """SELECT COUNT(*) FROM contacts c WHERE c.company_canon=? AND (c.status!='closed' OR
           EXISTS (SELECT 1 FROM outreach_messages m WHERE m.contact_id=c.id
                   AND m.direction='out' AND m.status IN ('sending','sent')))""", (canon,)).fetchone()[0]


def add_contact(con, *, company, name, title, profile_url, group, cfg, job_id=None,
                location=None, hook=None, sponsor_evidence=None):
    ensure_schema(con)
    c = settings(cfg)
    url = norm_profile_url(profile_url)
    canon = canon_company(company)
    if group not in ("applied", "target"):
        raise OutreachError("group must be 'applied' or 'target'")
    if con.execute("SELECT 1 FROM contacts WHERE profile_url=?", (url,)).fetchone():
        raise OutreachError("this profile is already in the contact list")
    if _listed(canon, c["exclude_companies"]):
        raise OutreachError(f"company '{company}' is excluded")
    if _company_count(con, canon) >= int(c["max_per_company"]):
        raise OutreachError(f"company '{company}' already has {c['max_per_company']} contacts")
    market = None
    if group == "target":
        entry = next((t for t in c["target_companies"] if canon_company(t["name"]) == canon), None)
        if entry is None:
            raise OutreachError(f"company '{company}' is not in target_companies")
        market = (entry.get("market") or "").lower() or None
        needs = [m.lower() for m in c["sponsor_required_markets"] or []]
        if market in needs and not _listed(canon, c["us_sponsors"]) \
                and len((sponsor_evidence or "").strip()) < 10:
            raise OutreachError(f"company '{company}' needs visa sponsorship evidence "
                                f"(us_sponsors or --sponsor-evidence)")
    else:
        if job_id is None:
            raise OutreachError("group 'applied' needs the job id of the application")
        job = con.execute("SELECT company, status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job is None or job["status"] != "applied":
            raise OutreachError(f"job {job_id} is not an applied role")
        if canon_company(job["company"]) != canon:
            raise OutreachError(f"job {job_id} belongs to another company")
    now = jobdb._now()
    cur = con.execute(
        """INSERT INTO contacts (company, company_canon, name, title, profile_url, location,
           market, grp, job_id, hook, sponsor_evidence, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,'found',?,?)""",
        (company, canon, name, title, url, location, market, group, job_id, hook,
         sponsor_evidence, now, now))
    jobdb._commit(con)
    return cur.lastrowid


def _set(con, cid, **fields):
    fields["updated_at"] = jobdb._now()
    cols = ",".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE contacts SET {cols} WHERE id=?", [*fields.values(), cid])


def verify_contact(con, contact_id, today):
    ensure_schema(con)
    row = _contact(con, contact_id)
    if row["status"] not in ("found", "ready"):
        raise OutreachError(f"contact {contact_id} is '{row['status']}', not found or ready")
    _set(con, contact_id, status="ready", verified_on=_d(today))
    jobdb._commit(con)


def set_contact_status(con, contact_id, status, *, reason=None, next_action_date=None):
    """Close a contact. Every forward move goes through verify_contact, mark_sent or log_in,
    so a status cannot be set by hand past the checks those steps make."""
    ensure_schema(con)
    row = _contact(con, contact_id)
    if status != "closed":
        raise OutreachError("only 'closed' can be set by hand; other statuses follow verify, "
                            "sends and logged replies")
    if row["status"] == "closed":
        raise OutreachError(f"contact {contact_id} is closed")
    if reason not in CLOSE_REASONS:
        raise OutreachError(f"closing needs a reason: {', '.join(CLOSE_REASONS)}")
    fields = {"status": status, "close_reason": reason}
    if next_action_date is not None:
        try:
            fields["next_action_date"] = datetime.date.fromisoformat(next_action_date).isoformat()
        except ValueError:
            raise OutreachError("next action date must be YYYY-MM-DD") from None
    _set(con, contact_id, **fields)
    jobdb._commit(con)


def set_thread_url(con, contact_id, url):
    ensure_schema(con)
    _contact(con, contact_id)
    host = (urlsplit(url).hostname or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        raise OutreachError("not a LinkedIn URL")
    _set(con, contact_id, thread_url=url)
    jobdb._commit(con)


# Messages

def _file_sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return "missing"


def action_hash(row):
    att = _file_sha(row["attachment_path"]) if row["attachment_path"] else ""
    text = f"{row['contact_id']}\n{row['kind']}\n{row['body']}\n{att}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_body(kind, body, c):
    if kind not in MESSAGE_KINDS:
        raise OutreachError(f"unknown kind {kind}")
    if "—" in body or "–" in body:
        raise OutreachError("text contains a long dash; use a plain hyphen or rephrase")
    if kind == "invite" and body:
        raise OutreachError("kind 'invite' is a request without a note; use 'invite_note'")
    if kind == "invite_note" and not (1 <= len(body) <= int(c["note_max_chars"])):
        raise OutreachError(f"invite note must be 1-{c['note_max_chars']} characters, "
                            f"got {len(body)}")
    if kind not in ("invite",) and not body.strip():
        raise OutreachError("empty text")


def _last_in_id(con, cid):
    r = con.execute("SELECT MAX(id) FROM outreach_messages WHERE contact_id=? AND direction='in'",
                    (cid,)).fetchone()[0]
    return r or 0


def _check_kind(con, contact, kind, c, today, exclude_id=None):
    cid, st = contact["id"], contact["status"]
    if st == "closed":
        raise OutreachError(f"contact {cid} is closed")
    if _listed(contact["company_canon"], c["exclude_companies"]):
        raise OutreachError(f"company '{contact['company']}' is excluded")
    if kind in INVITE_KINDS:
        if st != "ready":
            raise OutreachError(f"contact {cid} is '{st}'; invites go only to verified (ready) "
                                f"contacts")
        limit = _plus(today, -int(c["verify_max_age_days"]))
        if not contact["verified_on"] or contact["verified_on"] < limit:
            raise OutreachError(f"contact {cid} was verified more than "
                                f"{c['verify_max_age_days']} days ago; verify again")
        other = con.execute(
            f"""SELECT 1 FROM outreach_messages WHERE contact_id=? AND kind IN
                ('invite','invite_note') AND status!='failed' AND id!=?""",
            (cid, exclude_id or 0)).fetchone()
        if other:
            raise OutreachError(f"contact {cid} already has an invite")
    elif kind in ("message", "follow_up"):
        if st not in CONVERSATION:
            raise OutreachError(f"contact {cid} is '{st}'; messages need an accepted invite")
        if kind == "follow_up":
            done = con.execute(
                """SELECT 1 FROM outreach_messages WHERE contact_id=? AND kind='follow_up'
                   AND status IN ('sending','sent') AND id>? AND id!=?""",
                (cid, _last_in_id(con, cid), exclude_id or 0)).fetchone()
            if done:
                raise OutreachError(f"contact {cid} already had a reminder since their last "
                                    f"message")
    elif kind == "cv":
        if st != "talking":
            raise OutreachError(f"contact {cid} is '{st}'; a CV goes only into a conversation")


def _message(con, mid):
    row = con.execute("SELECT * FROM outreach_messages WHERE id=?", (mid,)).fetchone()
    if row is None:
        raise OutreachError(f"message {mid} not found")
    return row


def draft(con, contact_id, kind, body, cfg, today, *, attachment_path=None):
    ensure_schema(con)
    c = settings(cfg)
    body = body or ""
    _check_body(kind, body, c)
    contact = _contact(con, contact_id)
    _check_kind(con, contact, kind, c, today)
    att_sha = None
    if kind == "cv":
        if not attachment_path or not Path(attachment_path).is_file():
            raise OutreachError("a CV message needs an existing attachment file")
        att_sha = _file_sha(attachment_path)
    elif attachment_path:
        raise OutreachError("only a 'cv' message carries an attachment")
    cur = con.execute(
        """INSERT INTO outreach_messages (contact_id, direction, kind, body, attachment_path,
           attachment_sha256, status, created_at) VALUES (?, 'out', ?, ?, ?, ?, 'draft', ?)""",
        (contact_id, kind, body, attachment_path, att_sha, jobdb._now()))
    jobdb._commit(con)
    return cur.lastrowid


_CLEAR = "shown_hash=NULL, batch_token=NULL, approved_hash=NULL, approved_on=NULL"


def edit(con, message_id, body, cfg):
    ensure_schema(con)
    row = _message(con, message_id)
    if row["status"] not in ("draft", "shown", "approved", "failed"):
        raise OutreachError(f"message {message_id} is '{row['status']}' and cannot be edited")
    _check_body(row["kind"], body or "", settings(cfg))
    con.execute(f"UPDATE outreach_messages SET body=?, status='draft', {_CLEAR} WHERE id=?",
                (body or "", message_id))
    jobdb._commit(con)


def _expired(row, c, today):
    return not row["approved_on"] or \
        today > datetime.date.fromisoformat(row["approved_on"]) + \
        datetime.timedelta(days=int(c["approval_valid_days"]))


def requeue(con, message_id, cfg=None, today=None):
    ensure_schema(con)
    c = settings(cfg)
    row = _message(con, message_id)
    today = today or datetime.date.today()
    ok = row["status"] == "failed" or (row["status"] == "approved" and _expired(row, c, today))
    if not ok:
        raise OutreachError(f"message {message_id} is '{row['status']}'; only failed or expired "
                            f"approved messages go back to draft")
    con.execute(f"UPDATE outreach_messages SET status='draft', sent_on=NULL, {_CLEAR} WHERE id=?",
                (message_id,))
    jobdb._commit(con)


def make_batch(con, today):
    """Show every draft and every previously shown row; returns (token, rows)."""
    ensure_schema(con)
    rows = con.execute(
        """SELECT m.*, c.name, c.company, c.title FROM outreach_messages m
           JOIN contacts c ON c.id=m.contact_id
           WHERE m.status IN ('draft','shown') AND c.status!='closed' ORDER BY m.id""").fetchall()
    hashes = [(r["id"], action_hash(r)) for r in rows]
    token = hashlib.sha256(repr(sorted(hashes)).encode()).hexdigest()[:8] if rows else ""
    for mid, h in hashes:
        con.execute("""UPDATE outreach_messages SET status='shown', shown_hash=?, batch_token=?,
                       approved_hash=NULL, approved_on=NULL WHERE id=?""", (h, token, mid))
    jobdb._commit(con)
    return token, [dict(r) for r in rows]


def approve(con, token, message_ids, today):
    ensure_schema(con)
    rows = [_message(con, int(m)) for m in message_ids]
    for r in rows:
        if r["status"] != "shown" or r["batch_token"] != token:
            raise OutreachError(f"message {r['id']} is not in shown batch {token}; nothing approved")
        if action_hash(r) != r["shown_hash"]:
            raise OutreachError(f"message {r['id']} changed after it was shown; nothing approved")
    for r in rows:
        con.execute("""UPDATE outreach_messages SET status='approved', approved_hash=shown_hash,
                       approved_on=? WHERE id=?""", (_d(today), r["id"]))
    jobdb._commit(con)


def paused_until(con):
    ensure_schema(con)
    r = con.execute("SELECT value FROM outreach_state WHERE key='paused_until'").fetchone()
    return r[0] if r and r[0] else None


def _count_out(con, day_from, day_to, kinds=None):
    q = """SELECT COUNT(*) FROM outreach_messages WHERE direction='out'
           AND status IN ('sending','sent') AND sent_on>=? AND sent_on<=?"""
    args = [day_from, day_to]
    if kinds:
        q += f" AND kind IN ({','.join('?' for _ in kinds)})"
        args += list(kinds)
    return con.execute(q, args).fetchone()[0]


def _reserve(con, message_id, approved_hash, day=None):
    """Move one approved row to 'sending' only if no one else did: two claims of the same row,
    for example from parallel shell calls, cannot both win."""
    cur = con.execute("""UPDATE outreach_messages SET status='sending', sent_on=?
                         WHERE id=? AND status='approved' AND approved_hash=?""",
                      (day or _d(datetime.date.today()), message_id, approved_hash))
    if cur.rowcount != 1:
        con.rollback()
        raise OutreachError(f"message {message_id} was already claimed or changed")


def _begin_write(con):
    # Take the write lock before reading the limits, so two claims cannot both pass a cap.
    if not con.in_transaction:
        con.execute("BEGIN IMMEDIATE")


def claim(con, message_id, cfg, today):
    """Run every check before the browser click and reserve the send (status 'sending')."""
    ensure_schema(con)
    _begin_write(con)
    try:
        return _claim(con, message_id, cfg, today)
    except Exception:
        if con.in_transaction and id(con) not in jobdb._BATCH:
            con.rollback()
        raise


def _claim(con, message_id, cfg, today):
    c = settings(cfg)
    row = _message(con, message_id)
    if row["status"] != "approved":
        raise OutreachError(f"message {message_id} is '{row['status']}', not approved")
    if action_hash(row) != row["approved_hash"]:
        raise OutreachError(f"message {message_id} changed after approval; show it again")
    if _expired(row, c, today):
        raise OutreachError(f"approval of message {message_id} expired; requeue and show again")
    contact = _contact(con, row["contact_id"])
    _check_kind(con, contact, row["kind"], c, today, exclude_id=message_id)
    pu = paused_until(con)
    if pu and pu > _d(today):
        raise OutreachError(f"sending is paused until {pu}")
    if _count_out(con, _d(today), _d(today)) >= int(c["max_outgoing_per_day"]):
        raise OutreachError("daily limit of outgoing messages reached")
    if row["kind"] in INVITE_KINDS:
        if _count_out(con, _d(today), _d(today), INVITE_KINDS) >= int(c["daily_new_invites"]):
            raise OutreachError("daily limit of new invites reached")
        if _count_out(con, _plus(today, -6), _d(today), INVITE_KINDS) >= int(c["weekly_invites"]):
            raise OutreachError("weekly limit of invites reached")
    if row["attachment_path"] and _file_sha(row["attachment_path"]) != row["attachment_sha256"]:
        raise OutreachError(f"attachment of message {message_id} is missing or changed since "
                            f"the draft")
    _reserve(con, message_id, row["approved_hash"], _d(today))
    jobdb._commit(con)
    return {"message_id": message_id, "contact_id": contact["id"], "kind": row["kind"],
            "body": row["body"], "profile_url": contact["profile_url"],
            "thread_url": contact["thread_url"], "attachment_path": row["attachment_path"]}


_AFTER_SEND = {"invite": "invited", "invite_note": "invited", "message": "talking",
               "follow_up": None, "cv": "cv_sent"}


def mark_sent(con, message_id, today, cfg=None):
    ensure_schema(con)
    c = settings(cfg)
    row = _message(con, message_id)
    if row["status"] != "sending":
        raise OutreachError(f"message {message_id} is '{row['status']}'; claim it first")
    cur = con.execute("UPDATE outreach_messages SET status='sent' WHERE id=? AND status='sending'",
                      (message_id,))
    if cur.rowcount != 1:
        raise OutreachError(f"message {message_id} is no longer sending")
    contact = _contact(con, row["contact_id"])
    if contact["status"] != "closed":
        fields = {"next_action_date": _plus(today, c["follow_up_days"])}
        new = _AFTER_SEND[row["kind"]]
        if new == "talking" and contact["status"] == "cv_sent":
            new = None
        if new:
            fields["status"] = new
        _set(con, row["contact_id"], **fields)
    jobdb._commit(con)


def mark_failed(con, message_id):
    ensure_schema(con)
    row = _message(con, message_id)
    if row["status"] != "sending":
        raise OutreachError(f"message {message_id} is '{row['status']}', not sending")
    con.execute("""UPDATE outreach_messages SET status='failed', sent_on=NULL
                   WHERE id=? AND status='sending'""", (message_id,))
    jobdb._commit(con)


def pause(con, today, days=None, cfg=None):
    """Stop sending after a LinkedIn warning. Every approval is withdrawn, so whatever was left
    is shown to the user again after the pause."""
    ensure_schema(con)
    c = settings(cfg)
    until = _plus(today, days if days is not None else c["pause_days"])
    con.execute("INSERT OR REPLACE INTO outreach_state (key, value) VALUES ('paused_until', ?)",
                (until,))
    con.execute(f"""UPDATE outreach_messages SET status='draft', {_CLEAR}
                    WHERE status IN ('approved','shown')""")
    jobdb._commit(con)
    return datetime.date.fromisoformat(until)


def resume(con):
    ensure_schema(con)
    con.execute("DELETE FROM outreach_state WHERE key='paused_until'")
    jobdb._commit(con)


# Incoming

def log_in(con, contact_id, today, *, summary=None, accepted=False):
    ensure_schema(con)
    contact = _contact(con, contact_id)
    st = contact["status"]
    if st == "closed":
        raise OutreachError(f"contact {contact_id} is closed")
    if accepted:
        if st != "invited":
            raise OutreachError(f"contact {contact_id} is '{st}', not invited")
        kind, body, new = "accepted", summary or "accepted invitation", "connected"
    else:
        if not (summary or "").strip():
            raise OutreachError("a reply needs a one-line summary")
        if st not in ("invited",) + CONVERSATION:
            raise OutreachError(f"contact {contact_id} is '{st}'")
        kind, body = "reply", summary
        new = "cv_sent" if st == "cv_sent" else "talking"
    cur = con.execute(
        """INSERT INTO outreach_messages (contact_id, direction, kind, body, status, sent_on,
           created_at) VALUES (?, 'in', ?, ?, 'received', ?, ?)""",
        (contact_id, kind, body, _d(today), jobdb._now()))
    _set(con, contact_id, status=new, next_action_date=_d(today))
    jobdb._commit(con)
    return cur.lastrowid


# Daily due list

def _item(contact, message_id=None):
    out = {k: contact[k] for k in ("company", "name", "title", "profile_url", "thread_url",
                                   "hook")}
    out["contact_id"] = contact["id"]
    if message_id is not None:
        out["message_id"] = message_id
    return out


def due(con, cfg, today):
    """What needs doing today. Every "last" comparison uses row ids, so same-day order holds."""
    ensure_schema(con)
    c = settings(cfg)
    t = _d(today)
    contacts = {r["id"]: r for r in con.execute("SELECT * FROM contacts ORDER BY id")}
    msgs = {}
    for m in con.execute("SELECT * FROM outreach_messages ORDER BY id"):
        msgs.setdefault(m["contact_id"], []).append(m)
    res = {k: [] for k in ("uncertain", "relist", "pending", "first_messages", "replies",
                           "stale_invites", "follow_ups", "to_close", "new_invites",
                           "reverify")}

    for cid, ms in msgs.items():
        if contacts[cid]["status"] == "closed":
            continue
        for m in ms:
            if m["direction"] != "out":
                continue
            if m["status"] == "sending":
                res["uncertain"].append(_item(contacts[cid], m["id"]))
            elif m["status"] == "failed" or (m["status"] == "approved" and _expired(m, c, today)):
                res["relist"].append(_item(contacts[cid], m["id"]))
            elif m["status"] in ("draft", "shown", "approved"):
                res["pending"].append(_item(contacts[cid], m["id"]))

    for cid, contact in contacts.items():
        st = contact["status"]
        if st == "closed":
            continue
        ms = msgs.get(cid, [])
        ins = [m for m in ms if m["direction"] == "in"]
        sent = [m for m in ms if m["direction"] == "out" and m["status"] == "sent"]
        open_out = [m for m in ms if m["direction"] == "out"
                    and m["status"] in ("draft", "shown", "approved", "sending")]
        last_in = ins[-1] if ins else None
        last_sent_id = sent[-1]["id"] if sent else 0
        is_due = bool(contact["next_action_date"]) and contact["next_action_date"] <= t

        if st == "ready":
            limit = _plus(today, -int(c["verify_max_age_days"]))
            if not contact["verified_on"] or contact["verified_on"] < limit:
                res["reverify"].append(_item(contact))
            continue
        if open_out:
            continue
        if st == "connected" and last_in and last_in["kind"] == "accepted" \
                and last_in["id"] > last_sent_id:
            res["first_messages"].append(_item(contact))
        elif last_in and last_in["kind"] == "reply" and last_in["id"] > last_sent_id:
            res["replies"].append(_item(contact))
        elif st == "invited" and is_due:
            res["stale_invites"].append(_item(contact))
        elif st in CONVERSATION and is_due and last_sent_id > (last_in["id"] if last_in else 0):
            since = last_in["id"] if last_in else 0
            reminded = any(m["kind"] == "follow_up" and m["id"] > since for m in sent)
            res["to_close" if reminded else "follow_ups"].append(_item(contact))

    pu = paused_until(con)
    if not (pu and pu > t):
        used = _count_out(con, t, t, INVITE_KINDS) + con.execute(
            """SELECT COUNT(*) FROM outreach_messages WHERE kind IN ('invite','invite_note')
               AND status IN ('draft','shown','approved')""").fetchone()[0]
        room = max(0, int(c["daily_new_invites"]) - used)
        limit = _plus(today, -int(c["verify_max_age_days"]))
        fresh = [r for r in contacts.values() if r["status"] == "ready" and not msgs.get(r["id"])
                 and r["verified_on"] and r["verified_on"] >= limit]
        fresh.sort(key=lambda r: (r["grp"] != "applied", r["id"]))
        res["new_invites"] = [_item(r) for r in fresh[:room]]
    return res
