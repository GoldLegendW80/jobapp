# Profile and jobs data

Profile export and import, and the import of the owner's job list and files.

## Contents

- [Profile export and import](#profile-export-and-import)
- [Jobs import, list and files](#jobs-import-list-and-files)

## Profile export and import

`GET /api/profile/export` downloads the whole profile as `profile-YYYY-MM-DD.json`;
`POST /api/profile/import` takes that file back (2 MB at most) and replaces all seven sections in
one transaction, one revision each (a file written before `preferences` existed reads its
defaults, and the onboarding dates stored for the account are kept). Keys of the board engine
deleted on 2026-10-28 (`search.boards`, `search.feeds`, browser source rows other than LinkedIn
and Free-Work, `onboarding.starter_handled`) are ignored on read and dropped on the next save.
Harald's engine files convert into the same format on the Mac (the title and location regexes
of `engine/boards.json`, no boards), then load into one account inside the api container:

```bash
uv run --native-tls --directory api python -m app.profile.cli convert --source-dir "$PWD/.." --out /tmp/profile.json
docker compose exec -T api python -m app.profile.cli import --email you@example.org --file - < /tmp/profile.json
```

The import prints the new revision and the completeness, never the profile itself. Exit codes:
0 imported, 2 invalid arguments or file, 1 no such account or a database error.
`./deploy.sh profile import <email>` does both, the import on the Jetson.

## Jobs import, list and files

Harald's `../data` (`jobs.csv` and `jobs/<id>/`: posting.txt, cv.pdf, letter.pdf, cv.json,
letter.json, answers.json, review.json, apply.py; the HTML renders are skipped) converts on the Mac
into one `jobapp-jobs` document (about 18 MB, files in base64) and loads into one account inside
the api container, from stdin:

```bash
./deploy.sh jobs import you@example.org      # both steps, the import on the Jetson
uv run --native-tls --directory api python -m app.jobs.cli convert --source-dir "$PWD/../data" --out /tmp/jobs.json
docker compose exec -T api python -m app.jobs.cli import --email you@example.org --file - < /tmp/jobs.json
```

The CLI takes documents up to 64 MiB. `POST /api/jobs/import` takes the same document over HTTP
but only up to 10 MiB, Caddy's body limit: it is for small imports, Harald's data always goes
through the CLI. The import is idempotent: rows are matched by slug, then by the duplicate rules
(same URL, or same company, title and location); only changed fields are written, with one
`import` event naming them; files are deduped by SHA-256; nothing is ever deleted; and a row that
was sent (status applied, or an applied_on date) keeps its files byte for byte, a different file
under the same name being reported as a warning. It prints created, updated, unchanged, skipped,
files added, replaced, unchanged and kept, and the warnings (slugs and file names only).

Then the command line's CVs and letters become the postings' documents, once:
`./deploy.sh documents import-legacy you@example.org --dry-run` prints what would be imported,
the same without `--dry-run` imports it (`python -m app.documents.cli import-legacy` in the api
container). Each cv.json and letter.json becomes version 1 of the posting's CV or letter, template
classic; a sent posting keeps its cv.pdf and letter.pdf byte for byte, frozen; a JSON that does not
validate is kept as a draft with notes. Running it again changes nothing.

`GET /api/jobs` lists the caller's jobs with filters (`status` and `type` repeated, `company`,
`q` full text, `fit_min`, `fit_max`, `found_from`, `found_to`), `sort` (found_on, fit, company,
status, updated), `limit` and an opaque `cursor`; the answer carries `total`, `counts_by_status`
(every filter but status) and `fit_histogram` (10 buckets and unscored). `q` uses Postgres'
'simple' configuration: case is ignored but accents are not ("developpeur" does not find
"développeur"), and words match whole. `GET /api/jobs/{id}` gives the posting text, legacy columns,
files and the newest 50 events; `GET /api/jobs/{id}/files/{file_id}` downloads a file (PDFs inline,
the rest as attachments, ETag = SHA-256). Another user's ids answer 404.
