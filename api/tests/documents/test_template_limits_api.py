"""D16: the gallery gives the editor each template's counters (the soft limits of its manifest,
per kind), and a person's own template the default's until its row can be read (D25b)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.documents import templates
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-docs-limits-alex@example.org"


def test_each_template_names_its_counters_per_kind(
    factory: sessionmaker[Session], api: Api, clock: Any
) -> None:
    user = make_user(factory, email=ALEX, name="alex")
    token = api.login_token(ALEX)
    with factory() as db:
        db.execute(
            sa.text(
                "INSERT INTO document_user_templates (user_id, slug, name, manifest, cv_html, "
                "letter_html, css, font_families) VALUES "
                "(:u, 'upload-abcd1234', 'Mine', '{}', '', '', '', '{}')"
            ),
            {"u": user.id},
        )
        db.commit()
    answer = api.get("/api/document-templates", token=token)
    assert answer.status_code == 200
    gallery = {t["slug"]: t for t in answer.json()["templates"]}
    for slug, template in templates.registry().items():
        assert gallery[slug]["soft_limits"] == {
            kind: asdict(spec.soft_limits) for kind, spec in template.kinds.items()
        }
    classic = gallery["classic"]["soft_limits"]
    assert classic["cv"]["summary"] == 330
    assert classic["cv"]["main_bullet"] == 190
    assert classic["letter"]["paragraph"] == 900
    assert classic["letter"]["summary"] is None
    assert gallery["upload-abcd1234"]["user"] is True
    assert gallery["upload-abcd1234"]["soft_limits"] == gallery[templates.DEFAULT]["soft_limits"]
