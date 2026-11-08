"""The table of "Assess this posting" (app/jobs/assess.py, migration 0044): the last assessment
of each posting, replaced by the next one. `result` holds what the agent submitted, checked and
sorted by code, and the role clarity points code read from the posting at that moment."""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models_base import Base


class JobAssessment(Base):
    __tablename__ = "job_assessments"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["job_id", "user_id"],
            ["jobs.id", "jobs.user_id"],
            name="fk_job_assessments_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("jsonb_typeof(result) = 'object'", name="result_is_object"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False)
