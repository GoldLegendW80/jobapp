"""The agents of the jobs module, registered in every process that imports this file (a
registration module: app/worker.py `REGISTRATION_MODULES`): `jobs_assess` (assess.py),
`jobs_match` (match.py) and `posting_reader` (reader.py)."""

from app.jobs import assess as _assess  # noqa: F401
from app.jobs import match as _match  # noqa: F401
from app.jobs import reader as _reader  # noqa: F401
