"""Model registry: import every module's models here so `Base.metadata` holds all tables.

Alembic's env.py imports this module before comparing metadata with the database.
"""

from app.agents import models as agents_models
from app.browser import models as browser_models  # noqa: F401, registers the tables
from app.documents import models as documents_models  # noqa: F401, registers the tables
from app.identity import models as identity_models
from app.ingestion import models as ingestion_models
from app.ingestion import runs_models as ingestion_runs_models
from app.jobs import models as jobs_models
from app.mail import models as mail_models
from app.models_base import Base
from app.profile import models as profile_models

__all__ = [
    "Base",
    "agents_models",
    "identity_models",
    "ingestion_models",
    "ingestion_runs_models",
    "jobs_models",
    "mail_models",
    "profile_models",
]
