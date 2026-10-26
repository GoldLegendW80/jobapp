# The estimate of a run and the consent

What the app tells the person before a run reads documents, and the first time consent.

Before a run reads documents, the app says what it would read and what it may cost, and asks
for consent the first time (ingestion G16; the page that shows it is G11's).

- **`POST /api/ingestion/runs/estimate {document_ids, reread_document_ids?}`** answers the
  documents a run would read with their characters and images, the ones it cannot read and why,
  the steps, the model of the caller's credential (`claude-opus-5-5` on a Claude subscription,
  `claude-sonnet-5-5` on an Anthropic API key), the token range and the ceiling. No run starts.
  The answer carries a `quote_id`, a fingerprint of everything it shows. The start of a run
  sends it back and is refused with the estimate of that moment when it no longer matches
  (`estimate_router.check_echo`), so a run never starts on figures the user did not see.
- **The figures are estimates, computed in `api/app/ingestion/estimate.py`** from the sizes of
  the stored texts, never from their content: a low and a high number of tokens, and a ceiling
  of twice the high one, where a run pauses and asks before going on. On a subscription tokens
  are quota and no amount is shown. On an API key three amounts in US dollars are shown, rounded
  to the cent, with the date of the prices. The third, named `ceiling`, is the token ceiling
  priced as input tokens. It is not the most a run can cost, because output tokens cost more.
- **Prices are a dated table in `api/app/ingestion/pricing.py`**, not a live feed. To update it,
  read the provider's pricing page (its address is in the comment above `PRICES_AS_OF`) and
  change `PRICES` and `PRICES_AS_OF` in one commit, with the same figures in `api/tests/ingestion/test_pricing.py`, which asserts every
  price of every model as written on the page. A test also fails when a model a run can choose
  has no price.
- **Consent** is one row of `ingestion_consents` per user and version of the text. The texts
  are kept by version (`CONSENT_TEXTS` in `api/app/ingestion/estimate_router.py`) and the
  current version is the highest. `GET /api/ingestion/consent` answers the current text and
  whether the caller accepted it, `POST /api/ingestion/consent {version}` records it. To change
  the text, add the next version with the new words and its digest in
  `api/tests/ingestion/test_consent_text.py`: every user is asked again, and what was accepted
  before stays recorded with its date and its words. Editing the words of an existing version
  fails that test.

```bash
curl -s -X POST -H 'X-Requested-With: jobapp' -H 'Content-Type: application/json' -b cookies \
  -d '{"document_ids":["<id>"]}' http://localhost/api/ingestion/runs/estimate   # the estimate
curl -s -b cookies http://localhost/api/ingestion/consent                        # the text, accepted or not
curl -s -X POST -H 'X-Requested-With: jobapp' -H 'Content-Type: application/json' -b cookies \
  -d '{"version":1}' http://localhost/api/ingestion/consent                      # record it
```

A run that cannot move (its `advance` keeps failing) is closed by the cron after five failures in
a row (sixty for a failure of the database, the first one when the user asked to cancel it). The
operator can close it at once: `./deploy.sh ingestion close-run <run id> --dry-run` prints what
the close would do, the same without `--dry-run` closes it (`python -m app.ingestion.cli
close-run` in the api container): `failed` with the stuck sentence, or `cancelled` when the user
asked to cancel it, the planned steps cancelled, the running step's agent run asked to stop, the
steps read kept for the next run. It prints the status the run ends on. A run that has already
ended is left as it is, so running it again changes nothing.
