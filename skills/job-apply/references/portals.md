# portals.md : what is known about application sites

Kinds of sites met in real runs, and how to handle each. Add to this table whenever a run
finds something new (a quirk, a fix, a confirmed route); it saves the next run from
rediscovering it.

| Site or system | Own file upload | Account or code | Known quirks | How to confirm submission |
|---|---|---|---|---|
| Board one-click apply (for example LinkedIn Easy Apply) | Often no: only files already on the board profile | Signed-in board account | The tailored CV cannot be attached; a stored generic CV goes instead | Board's "applied" list |
| Reed | Yes, through the profile | Signed-in board account | Job links need the title slug in the URL; uploading replaces the profile's default CV | Page message, then email |
| CV-Library | Yes | Signed-in board account | Uploading replaces the profile's default CV | Page message, then email |
| Redirect trackers (for example click.appcast.io) | n/a | n/a | Redirects can fail; open the employer's own careers page for the role instead | n/a |
| Workday, Avature, iCIMS, SuccessFactors | Yes | Account required | Account wall: hand off | Email |
| Oracle recruiting cloud | Yes | Emailed one-time code | Never type the code: hand off | Email |
| Phenom-based career sites | Yes | Sometimes | "Save and continue" can freeze; reload once, then hand off | Page message |
| Greenhouse, Lever, Ashby, Teamtailor | Yes, file input visible | Usually none | Generally reliable; check custom questions for answers not in form_answers | Page message and email |
| Government job portals | Yes | Account required | Hand off | Portal dashboard |
| Any form | n/a | n/a | Dropdowns can show truncated labels; read the full option text before choosing | n/a |
| Any form | n/a | n/a | A value set by script can look filled but be empty on submit; read the form back | n/a |
| Micron Systems (lawcruit.micronapps.com, law firms) | Yes, but uploads live in iframes the browser tool cannot reach: the owner attaches files in Attachments | None | "Update Resume" parses the CV and overwrites form fields (it took a project for Employer 1): use the Attachments panel only. Address lines max 35 chars, Degree max 25. School is a type-ahead that auto-selects the first fuzzy match (a foreign school name can land on a US one): pick "Other". The US phone mask corrupts foreign numbers. | Form turns read-only with a guid; no thank-you text, confirm by email |
| LinkedIn Easy Apply | Yes: the file input is created on click and opens the OS picker; capture it by overriding HTMLInputElement.prototype.click, then upload | Signed-in LinkedIn | "Follow company" is ticked by default (untick); work history comes from the LinkedIn profile, not the CV | Board dialog and the Applied list |
| Workday (country Brazil) | Yes | Account required (owner creates it) | Requires Mother's and Father's family names; dropdowns ignore scripted values, click options; the Degree list can shift under the click, verify the chosen value | "Application Submitted" dialog, Candidate Home |
