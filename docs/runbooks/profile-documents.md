# Documents for the profile

The Fill my profile with an agent page and the documents step.

"Fill my profile with an agent" on the Profile page opens `/profile/fill` (SPEC-ingestion.md,
ingestion G1): three steps, of which the second, Documents, is built; the two others are listed as
"Soon" until their tasks land. A user adds files (dropped or chosen, sent one at a time with their
progress) or pastes a text, and sees each document with its type, size and state, the text kept
from it, and a Delete that asks first. Documents live in Postgres (`ingestion_documents`), no
other user of the app can see them (whoever runs the server can read its database and its
backups), and deleting one is a hard delete: gone from the app at once, and from the backups when
newer ones have replaced those that hold it.

- **Type:** decided from the first bytes of the file, never from its name or a declared type.
  Text (UTF-8, or UTF-16 with a BOM) is read at once and its bytes are not kept. A PDF, a ZIP
  (a DOCX or a LinkedIn export), a PNG or a JPEG is kept as it is with the state "Waiting": the
  parsers arrive with later tasks (`READABLE_KINDS` in `api/app/ingestion/documents.py`). Legacy
  Office, RTF, OpenDocument, HEIC, GIF, WebP and programs are refused with 415 and one sentence.
- **Limits:** 20 MiB per file, 100,000 characters per paste, 500,000 characters of text kept per
  document, 100 MB and 200 documents per user. The same file twice is one document (per user).
- **The upload route:** `POST /api/ingestion/documents` takes the file as the raw body, its name
  in `X-Filename` (RFC 5987), and is the one route on which Caddy accepts 20 MiB; every other
  route keeps 10 MiB (`caddy/Caddyfile`). The api reads the body into one buffer, counted and
  hashed as it arrives, and sends it to Postgres in pieces.
- **Who may send:** one body per user at a time (a file or a paste; a second one waits 35 s, then
  429; beyond two that wait, 429 at once), and two files at a time for the whole api process
  (then 503), so one account holds one of the two places at most. The wait is longer than the
  30 s below, so the file sent after an upload that a change of network cut off is taken, not
  refused. A body has to keep arriving: 256 KiB every 30 s and 10 minutes in
  all for a file, 60 s in all for a paste, else 408. The quota is checked before the bytes go to
  Postgres, and again under the user's lock.
- **Secrets:** a text that matches the shape of a password or an access key
  (`api/app/ingestion/secrets.py`) is flagged; the user checks it, keeps it or deletes it.
- **Logs:** ids, sizes, types and rule names only. Caddy drops `X-Filename` from its access log.

```bash
curl -s -X POST -H 'X-Requested-With: jobapp' -H "X-Filename: UTF-8''cv.pdf" -b cookies \
  --data-binary @cv.pdf http://localhost/api/ingestion/documents                                  # 201, or 200 when already there
curl -s -X POST -H 'X-Requested-With: jobapp' -H 'Content-Type: application/json' -b cookies \
  -d '{"text":"About me"}' http://localhost/api/ingestion/documents/text                          # a pasted text
curl -s -b cookies http://localhost/api/ingestion/documents                                       # the list, what it takes, the limits
curl -s -b cookies 'http://localhost/api/ingestion/documents/<id>/text?offset=0&limit=20000'      # the text kept
curl -s -X DELETE -H 'X-Requested-With: jobapp' -b cookies http://localhost/api/ingestion/documents/<id>
```
