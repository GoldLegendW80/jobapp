# Sweeps and the worker

How Find jobs searches the ticked job websites and how the worker runs it.

"Find jobs" on `/jobs` searches the job websites ticked in its setup, one model run per website
in the person's own browser (SPEC-jobs, interactive search), skips the postings already in the
list, stores the new ones as `found` and scores them; a posting matching a never apply rule
("Label: term, term") is stored as `skipped` with the rule as its note. The board readers
(Ashby, Greenhouse, Lever), the freelance feeds, the fixed readers (Google Careers, VIE, career
pages, LinkedIn's own reader) and the starter boards list were deleted (owner, 2026-10-28). The
work happens in the `worker` service (`python -m app.worker`, arq on Redis; its own image
`jobapp-worker`, see agent-runs.md), one sweep per user at a time. The page follows it live
(server-sent events, polling as the fallback): the progress bar "Websites searched" counts the
websites, each done once its phase is done, and the summary shows the new postings and each
website's outcome. Old sweep rows keep their board counts, readable in the history.
Automatic search is off by default: "Schedule" next to Find jobs (or the Search tab of the
profile) turns it on for weekdays, one to four local times and a time zone
(`search.sweep_schedule`), and shows when it runs next. The worker ticks every 5 minutes and starts each due slot once; a time skipped by daylight saving
time runs when the clock jumps, a repeated one runs once.

```bash
curl -s -X POST -H 'X-Requested-With: jobapp' -b cookies http://localhost/api/jobs/sweeps      # 202, 409 if one runs
curl -s -b cookies http://localhost/api/jobs/sweeps                                            # newest first, schedule
curl -s -N -b cookies http://localhost/api/jobs/sweeps/<id>/events                             # live progress
docker compose logs -f worker                                                                  # what the worker does
docker compose exec -T worker python -m app.worker --check                                     # its healthcheck
```

A sweep whose worker died is failed after two minutes without a heartbeat, so the next one can
start.

LinkedIn's 7 day pause after a security check (browser B10) has no button to lift it. Only the
owner can, by deleting the person's rows of `linkedin_checkpoints`:

```bash
ssh jetson "cd jobapp && docker compose -f compose.yml exec -T db sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'" <<< "
DELETE FROM linkedin_checkpoints WHERE user_id = (SELECT id FROM users WHERE email = 'e2e-someone@example.org');"
```
