"""Keep a chat correction as a writing rule, and the starter list of banned phrases
(SPEC-documents.md, "The chat, and keeping a rule", "Being careful"): the offer's accept and
refuse table, a fact never kept, the keep through a profile item accepted by the click, and the
starter list that never bans a phrase of the person's own CV."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.models import Proposal, ProposalDecider, ProposalStatus
from app.documents import checks, store, writing_rules
from app.documents import content as c
from app.documents.models import Kind, Role
from app.documents.settings import STARTER_PHRASES, DocumentSettings, banned_phrases
from app.documents.sources import sources_for
from app.profile.models import Profile
from tests.documents.conftest import base_cv, view_of
from tests.documents.render_support import cv_draft, give_profile
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-rules-alex@example.org"
BLAKE = "e2e-rules-blake@example.org"
# The block "Writing rules" of Alex's invented profile, before any rule is kept.
FIRST = "Plain words. No metric that this profile does not state."
BOTH = f"{FIRST}\nNever write leveraged\nAvoid synergy"

OFFERED = [
    "Never say leveraged",
    "never use the word 'passionate'",
    "Don't write spearheaded again",
    "Do not use exclamation marks",
    "Stop writing synergy",
    "Avoid buzzwords",
    "Shorter summary, please",
    "Use 'built' instead of 'developed'",
    "Rather than utilized, say used",
    "This sounds too formal",
    "Too long",
    "Fewer adjectives",
    "Always write the company name in full",
    "Only use plain words",
    "The tone is off, make it plainer",
    "No more cutting-edge",
    "Replace 'robust' with 'reliable'",
    "  Never   say\n leveraged  ",
    "N'écris jamais « passionné »",
    "n’utilise plus le mot dynamique",
    "Ne mets pas de point d'exclamation",
    "Évite le jargon",
    "Plus court",
    "Moins formel, plus direct",
    "Toujours écrire le nom de l'entreprise en entier",
    "Au lieu de « piloté », écris « mené »",
    "NEVER SAY LEVERAGED",
]

NOT_OFFERED = [
    # A fact about the person.
    "I led a team of 5 engineers",
    "Never say I have 10 years of Python",
    "Add that I speak German",
    "Mention that I worked at Example Corp",
    "Avoid saying my team was small",
    "My degree is a master, never say bachelor",
    "I'm a staff engineer, never write senior",
    "I also know Kubernetes, avoid saying otherwise",
    "Never write that I managed people",
    "Use 2021 instead of 2020",
    "J'ai dirigé une équipe, évite de le cacher",
    "Ajoute que je parle allemand",
    "Mentionne que j'ai un master",
    "Je parle anglais, évite le français",
    "Mon équipe était de huit personnes, jamais écrire dix",
    # Not a correction of the wording.
    "Use the sidebar template",
    "Put my most relevant experience first",
    "Make it fit on one page",
    "Thanks!",
    "What does this mean?",
    "Write it again",
    "",
    "   ",
    "Never say leveraged " * 20,
]


@pytest.mark.parametrize("text", OFFERED)
def test_a_wording_correction_is_offered_as_a_rule(text: str) -> None:
    assert writing_rules.offer(text) == " ".join(text.split())


@pytest.mark.parametrize("text", NOT_OFFERED)
def test_no_offer_for_a_fact_or_another_request(text: str) -> None:
    assert writing_rules.offer(text) is None


# --- the starter list ---------------------------------------------------------------------------


def test_the_starter_list_never_bans_a_phrase_of_the_person_own_cv() -> None:
    own = DocumentSettings(banned_phrases=("Leveraged", "synergy"))
    assert banned_phrases(own, ()) == ("Leveraged", "synergy")
    on = own.model_copy(update={"starter_phrases": True})
    found = banned_phrases(on, ["Spearheaded the move to Kubernetes.", "A pivotal year"])
    assert found[:2] == ("Leveraged", "synergy")
    assert "spearheaded" not in found
    assert "pivotal" not in found
    assert "leveraged" not in found  # the person's own already
    assert "spearheading" in found
    assert len(found) == 2 + len(STARTER_PHRASES) - 4
    assert banned_phrases(DocumentSettings(starter_phrases=True), ()) == STARTER_PHRASES


def test_the_checks_correct_a_starter_phrase_unless_the_base_cv_uses_it() -> None:
    written = c.CvContent(summary="Spearheaded the platform. Leveraged Terraform.")
    rules = DocumentSettings(starter_phrases=True)
    base = base_cv("alex")
    plain = sources_for(view_of("alex", base), base, None, None)
    found = checks.run("cv", written, plain, rules)
    assert sorted(
        written.summary[i.start : i.end] for i in found.claims if i.code == "banned_phrase"
    ) == ["Leveraged", "Spearheaded"]
    own = sources_for(
        view_of("alex", base), base, "I spearheaded the platform at Example Corp.", None
    )
    found = checks.run("cv", written, own, rules)
    assert [
        written.summary[i.start : i.end] for i in found.claims if i.code == "banned_phrase"
    ] == ["Leveraged"]
    off = checks.run("cv", written, plain, DocumentSettings())
    assert not [i for i in off.claims if i.code == "banned_phrase"]


def test_the_settings_route_reads_and_saves_the_switch(
    factory: sessionmaker[Session], api: Api, clock: Any
) -> None:
    alex, _ = _person(factory, api, ALEX, clock)
    read = api.get("/api/documents/settings", token=alex).json()
    assert read["starter_phrases"] is False
    assert read["starter_list"] == list(STARTER_PHRASES)
    saved = api.request(
        "PUT",
        "/api/documents/settings",
        token=alex,
        json={"no_dash_separators": False, "banned_phrases": [], "starter_phrases": True},
    )
    assert saved.status_code == 200, saved.text
    assert api.get("/api/documents/settings", token=alex).json()["starter_phrases"] is True
    refused = api.request(
        "PUT",
        "/api/documents/settings",
        token=alex,
        json={"no_dash_separators": False, "banned_phrases": [], "starter_list": []},
    )
    assert refused.status_code == 422


# --- keeping a rule -----------------------------------------------------------------------------


def _person(factory: sessionmaker[Session], api: Api, email: str, clock: Any) -> tuple[str, Any]:
    user = make_user(factory, email=email, name=email.split("@")[0])
    give_profile(factory, user.id, clock())
    return api.login_token(email), user.id


def _chat(
    factory: sessionmaker[Session], user: uuid.UUID, clock: Any
) -> tuple[uuid.UUID, int, int]:
    """A posting's CV with one message of the person and the assistant's answer."""
    with factory() as db:
        posting = db.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title) VALUES "
                "(:u, :s, 'Example Robotics', 'Platform engineer') RETURNING id"
            ),
            {"u": user, "s": f"robotics-{uuid.uuid4().hex[:8]}"},
        ).scalar_one()
        db.commit()
        document = cv_draft(db, user, posting, clock())
        asked = store.add_message(db, user, document, Role.user, "Never say leveraged", at=clock())
        answer = store.add_message(db, user, document, Role.assistant, "Done.", at=clock())
        db.commit()
        return document, asked.id, answer.id


def _rules_block(factory: sessionmaker[Session], user: uuid.UUID) -> dict[str, Any] | None:
    with factory() as db:
        sections = db.scalar(sa.select(Profile.sections).where(Profile.user_id == user))
    blocks = (sections or {}).get("facts", {}).get("blocks", [])
    return next((b for b in blocks if b["title"] == writing_rules.BLOCK_TITLE), None)


def test_the_chat_offers_and_keeps_a_rule(
    factory: sessionmaker[Session], api: Api, clock: Any
) -> None:
    alex, alex_id = _person(factory, api, ALEX, clock)
    blake, _ = _person(factory, api, BLAKE, clock)
    document, asked, answer = _chat(factory, alex_id, clock)

    messages = api.get(f"/api/documents/{document}/messages", token=alex).json()["messages"]
    assert [m["rule"] for m in messages] == ["Never say leveraged", None]
    assert _rules_block(factory, alex_id) == {"title": "Writing rules", "body": FIRST}

    path = f"/api/documents/{document}/messages/{asked}/rule"
    kept = api.post(path, token=alex, json={"text": "Never write  leveraged"})
    assert kept.status_code == 200, kept.text
    assert kept.json() == {"rule": "Never write leveraged", "kept": True}
    assert _rules_block(factory, alex_id) == {
        "title": "Writing rules",
        "body": f"{FIRST}\nNever write leveraged",
    }
    with factory() as db:
        proposal = db.scalars(sa.select(Proposal).where(Proposal.user_id == alex_id)).one()
        assert proposal.status is ProposalStatus.accepted
        assert proposal.decided_by is ProposalDecider.user
        assert proposal.payload["section"] == "facts"

    again = api.post(path, token=alex, json={"text": "never write leveraged"})
    assert again.json() == {"rule": "never write leveraged", "kept": False}
    second = api.post(path, token=alex, json={"text": "Avoid synergy"})
    assert second.json()["kept"] is True
    assert _rules_block(factory, alex_id) == {"title": "Writing rules", "body": BOTH}

    fact = api.post(path, token=alex, json={"text": "I led 8 engineers"})
    assert fact.status_code == 422
    assert "not what is true of you" in fact.json()["detail"]
    assert api.post(path, token=alex, json={"text": "  "}).status_code == 422
    assert api.post(path, token=alex, json={"text": "x" * 301}).status_code == 422
    assert _rules_block(factory, alex_id) == {"title": "Writing rules", "body": BOTH}

    assert api.post(path, token=blake, json={"text": "Avoid synergy"}).status_code == 404
    own_answer = f"/api/documents/{document}/messages/{answer}/rule"
    assert api.post(own_answer, token=alex, json={"text": "Avoid hype"}).status_code == 404
    with factory() as db:
        letter = store.get_or_create(
            db,
            alex_id,
            store.get(db, alex_id, document).posting_id,
            Kind.letter,
            at=clock(),  # type: ignore[union-attr, arg-type]
        )
        db.commit()
        other = f"/api/documents/{letter.id}/messages/{asked}/rule"
    assert api.post(other, token=alex, json={"text": "Avoid hype"}).status_code == 404
