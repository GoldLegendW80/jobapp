"""Model registry: import every module's models here so `Base.metadata` holds all tables.

Alembic's env.py imports this module before comparing metadata with the database.
"""

from app.identity import models as identity_models
from app.models_base import Base
from app.profile import models as profile_models

__all__ = ["Base", "identity_models", "profile_models"]
