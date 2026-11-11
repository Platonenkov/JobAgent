# Recruiter outreach (example)

Copy this file to `config/outreach.md` and fill in your own lists. The outreach CLI refuses to
run without `config/outreach.md`: an example list of companies must never drive real messages.

- `target_companies`: companies you want to meet recruiters at, each with its market. The market
  comes from here, never from the assistant, so a US company cannot be passed off as another.
- `us_sponsors`: companies you have confirmed sponsor work visas (advert text or public filings).
  A target in a market listed in `sponsor_required_markets` needs an entry here or written
  evidence on the contact.
- `exclude_companies`: never contact these (staffing firms, companies you ruled out).
- Limits: new invites per day and per rolling 7 days, outgoing messages per day, people per
  company over the whole search, days before one reminder, how long an approval stays valid,
  how long sending pauses after a warning, the invitation note length, and how old a check of
  someone's current employer may be before an invite.

```yaml
outreach:
  target_companies:
    - {name: Example Corp, market: br}
    - {name: Sample Inc, market: us}
  us_sponsors: []
  exclude_companies: [Example Staffing]
  sponsor_required_markets: [us]
  daily_new_invites: 5
  weekly_invites: 80
  max_outgoing_per_day: 10
  max_per_company: 2
  follow_up_days: 7
  approval_valid_days: 1
  pause_days: 7
  note_max_chars: 200
  verify_max_age_days: 14
```
