"""Job alert e-mails as a Find jobs source (SPEC-mail.md, "Job alert e-mails: a narrow
exception"; owner, 2026-10-30, "Gmail job alerts").

A job website's own alert e-mail (Indeed first: it has no API and its pages sit behind a
security check) lists the postings of a search the person saved there. When the person turns a
provider on in Find jobs setup and has connected Gmail, a sweep reads those alert e-mails, and
nothing else of the mailbox, and stores the postings they list through the sweep's one commit
path, so they are filtered, deduplicated and scored like every other finding.

- `providers.py`: the allow list, per provider: the exact sender addresses, whether each was
  verified from a public source, and the parser. A provider without a parser is not listed.
- `text.py`: the shared line and link helpers of the parsers.
- `indeed.py`, `linkedin.py`: one deterministic parser per provider. Pure functions, the standard
  library only, never a model: the mail is a stranger's text.
- `source.py`: the sweep's side: which messages are read, how a message is admitted (an allowed
  sender that Gmail authenticated, `mail/auth.py` through `mail/parse.py`), the cursor, the
  stats entry of each provider, and the hand over to `sweep.Commits`.

No link of an alert is ever fetched: a parser reads the job's id from the link's text and builds
the canonical address the person opens in her own browser.
"""
