"""Outreach bookkeeping: contacts, approvals, the pre-send guard and the daily due list.
All names and companies here are fictional."""
import datetime
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import jobdb, outreachdb as od  # noqa: E402
from lib.outreachdb import OutreachError  # noqa: E402

D = datetime.date(2026, 11, 10)
URL = "https://www.linkedin.com/in/ana-souza"


def cfg(**over):
    c = {
        "daily_new_invites": 5, "weekly_invites": 80, "max_outgoing_per_day": 10,
        "max_per_company": 2, "follow_up_days": 7, "approval_valid_days": 1, "pause_days": 7,
        "note_max_chars": 200, "verify_max_age_days": 14, "sponsor_required_markets": ["us"],
        "target_companies": [{"name": "Example Corp", "market": "br"},
                             {"name": "Sample Inc", "market": "us"}],
        "exclude_companies": ["Acme Staffing"],
        "us_sponsors": [],
    }
    c.update(over)
    return c


@pytest.fixture
def con(tmp_path):
    c = jobdb.connect(tmp_path / "t.db")
    jobdb.insert_job(c, {"source": "test", "job_id": "1", "title": "Dev", "company": "Example Corp",
                         "location": "Remote"})
    jobdb.update_status(c, 1, "applied")
    jobdb.insert_job(c, {"source": "test", "job_id": "2", "title": "Dev", "company": "Other Ltd",
                         "location": "Remote"})
    jobdb.update_status(c, 2, "applied")
    return c


def add(con, slug="ana-souza", company="Example Corp", group="target", c=None, **kw):
    return od.add_contact(con, company=company, name="Ana Souza", title="Technical Recruiter",
                          profile_url=f"https://www.linkedin.com/in/{slug}", group=group,
                          cfg=c or cfg(), **kw)


def status(con, cid):
    return con.execute("SELECT status FROM contacts WHERE id=?", (cid,)).fetchone()[0]


# Task 1

def test_add_contact_starts_found(con):
    cid = add(con)
    assert isinstance(cid, int)
    assert status(con, cid) == "found"


def test_profile_url_variants(con):
    variants = ["https://br.linkedin.com/in/Ana-Souza/en/?trk=x",
                "http://m.linkedin.com/in/ana-souza#a",
                "https://www.linkedin.com/in/ana-souza/details/experience/",
                "https://linkedin.com/in/ana%2Dsouza"]
    for v in variants:
        assert od.norm_profile_url(v) == URL
    add(con)
    for v in variants:
        with pytest.raises(OutreachError):
            od.add_contact(con, company="Example Corp", name="A", title="R", profile_url=v,
                           group="target", cfg=cfg())
    for bad in ["https://evil.example/in/x", "https://www.linkedin.com/company/example",
                "https://www.linkedin.com/in/ACoAAA123"]:
        with pytest.raises(OutreachError):
            od.norm_profile_url(bad)


def test_company_variants(con):
    assert (od.canon_company("Example Corporation") == od.canon_company("Example Corp.")
            == od.canon_company("example.com Services LLC") == "example")
    with pytest.raises(OutreachError):
        add(con, company="Acme Staffing, Inc.",
            c=cfg(target_companies=[{"name": "Acme Staffing", "market": "br"}]))


def _send_invite(con, cid, day=D):
    od.verify_contact(con, cid, day)
    mid = od.draft(con, cid, "invite", "", cfg(), day)
    token, _ = od.make_batch(con, day)
    od.approve(con, token, [mid], day)
    od.claim(con, mid, cfg(), day)
    od.mark_sent(con, mid, day)
    return mid


def test_lifetime_company_cap(con):
    a = add(con, "a")
    b = add(con, "b")
    _send_invite(con, a)
    od.set_contact_status(con, a, "closed", reason="no_reply")
    with pytest.raises(OutreachError):
        add(con, "c")
    od.set_contact_status(con, b, "closed", reason="owner_skip")
    assert add(con, "c")


def test_target_must_be_listed(con):
    with pytest.raises(OutreachError):
        add(con, company="Other Ltd")


def test_us_target_needs_sponsor(con):
    with pytest.raises(OutreachError):
        add(con, company="Sample Inc")
    assert add(con, "b", company="Sample Inc", sponsor_evidence="advert says visa sponsorship")
    assert add(con, "c", company="Sample Inc", c=cfg(us_sponsors=["Sample Inc"]))


def test_applied_group_needs_matching_job(con):
    with pytest.raises(OutreachError):
        add(con, group="applied")
    with pytest.raises(OutreachError):
        add(con, group="applied", job_id=2)
    assert add(con, group="applied", job_id=1)


def test_close_requires_reason_and_is_final(con):
    cid = add(con)
    with pytest.raises(OutreachError):
        od.set_contact_status(con, cid, "closed")
    od.set_contact_status(con, cid, "closed", reason="not_relevant")
    with pytest.raises(OutreachError):
        od.set_contact_status(con, cid, "ready")


def test_ensure_schema_does_not_commit_open_write(con):
    con.execute("UPDATE jobs SET notes='x' WHERE id=1")
    od.ensure_schema(con)
    con.rollback()
    assert con.execute("SELECT notes FROM jobs WHERE id=1").fetchone()[0] is None


# Task 2

def ready(con, slug="ana-souza", day=D):
    cid = add(con, slug)
    od.verify_contact(con, cid, day)
    return cid


def approve_one(con, mid, day=D):
    token, _ = od.make_batch(con, day)
    od.approve(con, token, [mid], day)


def send(con, mid, day=D, c=None):
    approve_one(con, mid, day)
    od.claim(con, mid, c or cfg(), day)
    od.mark_sent(con, mid, day)


def connect(con, cid, day=D):
    mid = od.draft(con, cid, "invite", "", cfg(), day)
    send(con, mid, day)
    od.log_in(con, cid, day, accepted=True)


def test_draft_refuses_dashes_and_bad_lengths(con):
    cid = ready(con)
    for body in ["Hi — there", "Hi – there"]:
        with pytest.raises(OutreachError):
            od.draft(con, cid, "invite_note", body, cfg(), D)
    with pytest.raises(OutreachError):
        od.draft(con, cid, "invite_note", "x" * 201, cfg(), D)
    with pytest.raises(OutreachError):
        od.draft(con, cid, "invite", "has a body", cfg(), D)
    assert od.draft(con, cid, "invite_note", "x" * 200, cfg(), D)


def test_kind_rules_by_contact_status(con):
    found = add(con, "found")
    with pytest.raises(OutreachError):
        od.draft(con, found, "invite", "", cfg(), D)
    old = add(con, "old", company="Sample Inc", sponsor_evidence="advert says visa sponsorship")
    od.verify_contact(con, old, D - datetime.timedelta(days=15))
    with pytest.raises(OutreachError):
        od.draft(con, old, "invite", "", cfg(), D)
    cid = add(con, "b", company="Sample Inc", sponsor_evidence="advert says visa sponsorship")
    od.verify_contact(con, cid, D)
    send(con, od.draft(con, cid, "invite", "", cfg(), D))
    with pytest.raises(OutreachError):
        od.draft(con, cid, "invite", "", cfg(), D)
    with pytest.raises(OutreachError):
        od.draft(con, cid, "message", "Hello", cfg(), D)
    od.set_contact_status(con, cid, "closed", reason="declined")
    with pytest.raises(OutreachError):
        od.draft(con, cid, "follow_up", "Hello", cfg(), D)


def test_full_send_moves_contact(con):
    cid = ready(con)
    send(con, od.draft(con, cid, "invite", "", cfg(), D))
    row = con.execute("SELECT status, next_action_date FROM contacts WHERE id=?", (cid,)).fetchone()
    assert row["status"] == "invited"
    assert row["next_action_date"] == (D + datetime.timedelta(days=7)).isoformat()


def test_approve_refuses_text_changed_after_batch(con):
    a = od.draft(con, ready(con, "a"), "invite_note", "Hi Ana", cfg(), D)
    b = od.draft(con, ready(con, "b"), "invite_note", "Hi Bia", cfg(), D)
    token, _ = od.make_batch(con, D)
    con.execute("UPDATE outreach_messages SET body='Hire me' WHERE id=?", (a,))
    with pytest.raises(OutreachError):
        od.approve(con, token, [a, b], D)
    st = [r[0] for r in con.execute("SELECT status FROM outreach_messages ORDER BY id")]
    assert st == ["shown", "shown"]


def test_approve_refuses_wrong_token_or_unshown_id(con):
    a = od.draft(con, ready(con, "a"), "invite", "", cfg(), D)
    token, _ = od.make_batch(con, D)
    with pytest.raises(OutreachError):
        od.approve(con, "deadbeef", [a], D)
    b = od.draft(con, ready(con, "b"), "invite", "", cfg(), D)
    with pytest.raises(OutreachError):
        od.approve(con, token, [a, b], D)


def test_claim_refuses_changed_text(con):
    mid = od.draft(con, ready(con), "invite_note", "Hi Ana", cfg(), D)
    approve_one(con, mid)
    con.execute("UPDATE outreach_messages SET body='Hire me' WHERE id=?", (mid,))
    with pytest.raises(OutreachError):
        od.claim(con, mid, cfg(), D)


def test_approval_expires(con):
    a = od.draft(con, ready(con, "a"), "invite", "", cfg(), D)
    b = od.draft(con, ready(con, "b"), "invite", "", cfg(), D)
    token, _ = od.make_batch(con, D)
    od.approve(con, token, [a, b], D)
    od.claim(con, a, cfg(), D + datetime.timedelta(days=1))
    d2 = D + datetime.timedelta(days=2)
    with pytest.raises(OutreachError):
        od.claim(con, b, cfg(), d2)
    od.requeue(con, b, today=d2)
    approve_one(con, b, d2)
    od.claim(con, b, cfg(), d2)


def test_pause_resets_approvals(con):
    mid = od.draft(con, ready(con), "invite", "", cfg(), D)
    approve_one(con, mid)
    od.pause(con, D)
    assert con.execute("SELECT status FROM outreach_messages").fetchone()[0] == "draft"
    with pytest.raises(OutreachError):
        od.claim(con, mid, cfg(), D)
    d7 = D + datetime.timedelta(days=7)
    od.verify_contact(con, 1, d7)
    approve_one(con, mid, d7)
    od.claim(con, mid, cfg(), d7)


def test_pause_blocks_claim_until_date(con):
    mid = od.draft(con, ready(con), "invite", "", cfg(), D)
    od.pause(con, D)
    approve_one(con, mid, D + datetime.timedelta(days=3))
    with pytest.raises(OutreachError):
        od.claim(con, mid, cfg(), D + datetime.timedelta(days=3))


def test_caps_count_claims(con):
    c = cfg(max_per_company=50, target_companies=[{"name": "Example Corp", "market": "br"}])
    ids = []
    for i in range(6):
        cid = add(con, f"p{i}", c=c)
        od.verify_contact(con, cid, D)
        ids.append(od.draft(con, cid, "invite", "", c, D))
    token, _ = od.make_batch(con, D)
    od.approve(con, token, ids, D)
    for mid in ids[:5]:
        od.claim(con, mid, c, D)
    with pytest.raises(OutreachError):
        od.claim(con, ids[5], c, D)
    with pytest.raises(OutreachError):
        od.claim(con, ids[5], cfg(**{**c, "daily_new_invites": 50, "max_outgoing_per_day": 5}), D)
    with pytest.raises(OutreachError):
        od.claim(con, ids[5], cfg(**{**c, "daily_new_invites": 50, "weekly_invites": 5}),
                 D + datetime.timedelta(days=1))


def test_sending_row_never_relisted(con):
    mid = od.draft(con, ready(con), "invite", "", cfg(), D)
    approve_one(con, mid)
    od.claim(con, mid, cfg(), D)
    _, rows = od.make_batch(con, D)
    assert mid not in [r["id"] for r in rows]
    with pytest.raises(OutreachError):
        od.claim(con, mid, cfg(), D)


def test_mark_failed_allows_requeue(con):
    mid = od.draft(con, ready(con), "invite", "", cfg(), D)
    approve_one(con, mid)
    od.claim(con, mid, cfg(), D)
    od.mark_failed(con, mid)
    od.requeue(con, mid)
    send(con, mid)


def test_cv_hash_covers_attachment(con, tmp_path):
    cid = ready(con)
    connect(con, cid)
    send(con, od.draft(con, cid, "message", "Hi Ana, how is it going?", cfg(), D))
    od.log_in(con, cid, D, summary="asked about my background")
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"one")
    mid = od.draft(con, cid, "cv", "Here is my CV.", cfg(), D, attachment_path=str(pdf))
    approve_one(con, mid)
    pdf.write_bytes(b"two")
    with pytest.raises(OutreachError):
        od.claim(con, mid, cfg(), D)
