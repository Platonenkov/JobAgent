---
name: recruiter-outreach
description: Daily outreach to technical recruiters on LinkedIn for the user's job search, through Claude in Chrome in the user's signed-in session: keeps a list of recruiters at target companies and at employers already applied to, verifies them, drafts short human messages (an introduction, not a job request), shows one batch a day for the user's approval, and sends only the approved text after a pre-send check by scripts/outreach.py. Use for "daily recruiter outreach", "find recruiters at X", "show today's outreach batch", "send the approved outreach batch", or "check LinkedIn replies".
---

# recruiter-outreach

A person inside a company can look at an application, mention roles that are not advertised, or
pass a CV to the person who decides. This skill meets technical recruiters the way a person
would: an introduction and some interest in their work first, the CV only when the conversation
gets there. Every message goes out in the user's name, so nothing is sent without their approval.

`scripts/outreach.py` keeps the books in jobs.db (tables `contacts`, `outreach_messages`,
`outreach_state`, described in skills/app-tracker/references/db_contract.md). It never touches
LinkedIn: you act in the browser and record each step, and it refuses a step that breaks a rule.
Run commands from the project root (see job-pipeline); results are JSON, a refusal exits with
code 2 and a reason on stderr. Settings: config/outreach.md (copy config/outreach.example.md).

## Rules that are never relaxed
- Send only text the user approved in the current batch (see "Approval"). The CLI enforces the
  hash of what was shown; you enforce that `approve` follows the user's own yes.
- Never type passwords or one-time codes, never solve or bypass a CAPTCHA.
- On any LinkedIn warning, restriction notice, CAPTCHA or "unusual activity" page: stop at once,
  run `outreach.py pause`, tell the user. Never retry.
- Never contact staffing firms or anyone at a company in `exclude_companies`.
- Facts about the user come only from the master profile. Never name what config/application_rules.md
  lists under `never_mention`. Never invent shared interests, mutual contacts or a hook.
- Writing: plain, friendly English at the user's level (no showy vocabulary), no em or en
  dashes, every text written for its person (no template with a name swapped in).

## LinkedIn pace and privacy
- At most 30 profile views and 10 people searches a day; tens of seconds between sends.
- Open a conversation only from the listed contact's profile ("Message") or their stored
  `thread_url`; record a new thread link with `set-thread`. Never browse the inbox list, never
  open or read another thread, never record anything about people outside the list.
- Store an incoming message as a one-line summary (`log-in --summary`), not the full text.

## Before and after a session
- The tracker is shared through the user's data repository: pull it before you start, commit
  `jobs.db` when you finish, and work on one machine at a time.

## The daily cycle
1. **Open items**: `outreach.py due`.
   - `uncertain`: a send whose outcome is unknown (claimed, never confirmed). Open that
     conversation, check whether it went out, then `mark-sent` or `mark-failed`. Do this first.
   - `relist`: expired approvals and failed sends. `requeue --id N`, then they go into the batch.
2. **Incoming**: for the contacts in `invited`, `connected`, `talking` or `cv_sent`, open each
   one's conversation (profile or `thread_url`) and record what is new:
   `log-in --contact N --accepted` for an accepted invite, `log-in --contact N --summary "..."`
   for a reply. Run `due` again.
3. **Top up the list** (every few days, or when `new_invites` runs short):
   - Search people at a company from `target_companies`, or at an employer the user applied to
     (tracker status `applied`). Look for technical recruiters and talent acquisition people
     who hire engineers, best of all someone hiring in the user's region or who posted the role.
   - `add-contact --company --name --title --profile-url --group target|applied
     [--job-id N] [--hook "..."] [--sponsor-evidence "..."]`. The CLI refuses duplicates,
     excluded companies, a company over its limit and a US target without sponsorship evidence.
   - Open the profile, confirm the person still works there and find a real hook (a recent
     post, company news, the product, a shared city). Then `verify --id N`. A contact listed
     under `reverify` needs this again before an invite.
4. **Drafts**, from `due`:
   - `new_invites`: an invite. Check the personalised-note allowance in LinkedIn's invite
     dialog. Within it, `draft --kind invite_note --body-file f` (at most `note_max_chars`
     characters); over it, `draft --kind invite` with no body and write after acceptance.
   - `first_messages`: an accepted invite. A first message about them and their work: how they
     like it there, what the team is building, how remote work goes.
   - `replies`: answer what they said, keep it short.
   - `follow_ups`: one gentle reminder, then nothing more. `to_close`: close with
     `set-status --id N --status closed --reason no_reply`.
   - `stale_invites`: an invite not accepted after the reminder period. LinkedIn does not allow
     messaging someone outside the user's connections, so ask the user: wait longer, or close
     with `--reason invite_ignored`.
   - A declined or uninterested person: thank them briefly, then close with `--reason declined`.
5. **The batch**: `outreach.py batch` prints a token and a table. Show the user the token, the
   table, and for each row why it is going (the hook, or what they said).
6. **Approval**: run `approve --token T --ids ...` only right after the user's explicit yes to
   that token, for the ids they approved. Changes they ask for: `edit --id N --body-file f`,
   then a new `batch` and a new yes, unless the user dictated the exact new wording.
7. **Sending**, one row at a time in the order of the table:
   1. `claim --id N`. A refusal (limit, expiry, pause, changed text) means do not send; report it.
   2. In the browser, send exactly the claimed text to the claimed profile or thread. An invite
      is "Connect" (with "Add a note" for `invite_note`); a message goes in the conversation;
      a CV is the claimed PDF attached to the message.
   3. `mark-sent --id N` when the page shows it went; `mark-failed --id N` when the page shows
      it did not. If you cannot tell, leave it claimed and report it: it shows as `uncertain`.
8. **Report** to the user: what was sent, new acceptances and replies, what waits for them.

## Moving to the CV
Only when the conversation got there (they asked about the user's background or open roles, or
offered to pass a CV on), propose it in the batch: why now, and which role. After the user's
yes, build the CV for that company with the user's CV tool, then
`draft --kind cv --body-file f --attachment <pdf>`; it goes through the next batch like any
other message. The attachment is part of the approved action: a rebuilt PDF needs a new batch.

## When something is off
- A refusal from the CLI is a rule doing its job: report it, do not work around it.
- After a pause, every approval was withdrawn: show the drafts again when the pause ends.
