"""Model registry: import every module's models here so `Base.metadata` holds all tables.

Alembic's env.py imports this module before comparing metadata with the database.
Example, once the identity module exists: `from app.identity import models as identity_models`.
"""

from app.models_base import Base

__all__ = ["Base"]
