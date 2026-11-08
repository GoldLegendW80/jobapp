"""The agents of the jobs module, registered in every process that imports this file (a
registration module: app/worker.py `REGISTRATION_MODULES`): `jobs_assess` (assess.py)."""

from app.jobs import assess as _assess  # noqa: F401
