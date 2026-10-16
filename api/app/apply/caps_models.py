"""Tables of apply A11 (migration 0019, alembic/versions/0019_apply_settings.py; SPEC-apply.md,
"Guardrails", 5, and "Frontend", Settings).

- `apply_company_caps`: a person's own limit for one company ("Example Robotics: at most 3
  applications in 90 days, and 180 days before the same role"), one row per normalised company
  key (`caps.company_key`). `same_role_days` is null when the person set no cool down.
- `apply_settings`: one row per person, written on the first change; a person without a row reads
  the defaults. A null `min_fit`, `company_days`, `safety_ceiling` or `hold_s` reads the default of
  the settings (`caps.settings_of`), so a person who never chose one follows a changed default.
"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from app.models_base import Base

LABEL_MAX = 200
COUNT_MAX = 100
DAYS_MAX = 3650


class LikeMode(enum.StrEnum):
    review = "review"
    full_auto = "full_auto"


LIKE_MODE_ENUM = sa.Enum(
    LikeMode, name="apply_like_mode", values_callable=lambda members: [m.value for m in members]
)


def _user_fk(primary_key: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=primary_key,
        nullable=False,
    )


def _now() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


class CompanyCap(Base):
    __tablename__ = "apply_company_caps"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "company_key", name="uq_apply_company_caps_user_company"),
        sa.CheckConstraint(
            f"char_length(company_key) BETWEEN 1 AND {LABEL_MAX}", name="company_key_length"
        ),
        sa.CheckConstraint(f"char_length(label) BETWEEN 1 AND {LABEL_MAX}", name="label_length"),
        sa.CheckConstraint(f"max_count BETWEEN 1 AND {COUNT_MAX}", name="max_count_range"),
        sa.CheckConstraint(f"window_days BETWEEN 1 AND {DAYS_MAX}", name="window_days_range"),
        sa.CheckConstraint(
            f"same_role_days IS NULL OR same_role_days BETWEEN 1 AND {DAYS_MAX}",
            name="same_role_days_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = _user_fk()
    company_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    label: Mapped[str] = mapped_column(sa.Text, nullable=False)
    max_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    window_days: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    same_role_days: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = _now()


class ApplySettings(Base):
    __tablename__ = "apply_settings"
    __table_args__ = (
        sa.CheckConstraint("min_fit IS NULL OR min_fit BETWEEN 0 AND 100", name="min_fit_range"),
        sa.CheckConstraint("rehearsals_done >= 0", name="rehearsals_done_not_negative"),
        sa.CheckConstraint(
            "company_days IS NULL OR company_days BETWEEN 1 AND 365", name="company_days_range"
        ),
        sa.CheckConstraint(
            "safety_ceiling IS NULL OR safety_ceiling BETWEEN 1 AND 100",
            name="safety_ceiling_range",
        ),
        sa.CheckConstraint("hold_s IS NULL OR hold_s BETWEEN 60 AND 86400", name="hold_s_range"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk(primary_key=True)
    mode_after_like: Mapped[LikeMode] = mapped_column(
        LIKE_MODE_ENUM,
        nullable=False,
        default=LikeMode.review,
        server_default=LikeMode.review.value,
    )
    paused: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    full_auto_since: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    rehearsals_done: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    min_fit: Mapped[int | None] = mapped_column(sa.SmallInteger)
    company_days: Mapped[int | None] = mapped_column(sa.Integer)
    safety_ceiling: Mapped[int | None] = mapped_column(sa.Integer)
    hold_s: Mapped[int | None] = mapped_column(sa.Integer)
    updated_at: Mapped[datetime] = _now()
