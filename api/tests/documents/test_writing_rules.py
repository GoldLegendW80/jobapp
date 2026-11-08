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

# The accept table: what the chat offers, and Keep keeps, as it is (white space collapsed).
ACCEPTED = [
    "Never say leveraged",
    "never use the word 'passionate'",
    "Don't write spearheaded again",
    "Do not use exclamation marks",
    "Do not use the word \u201cpassionate\u201d in the summary",
    "Stop writing synergy",
    "Avoid buzzwords",
    "Never mention Kubernetes",  # never plus a verb plus a short phrase
    "Never ever say leveraged",
    "Also, never say leveraged",
    'Never say "leveraged"',
    "No more cutting-edge",
    "No dashes",
    "Shorter summary, please",
    "Thank you, shorter please",
    "Less formal, please",
    "This sounds too formal",
    "Too long",
    "Fewer adjectives",
    "Only use plain words",
    "Always write shorter sentences",
    "Use bullet points",
    "Use the active voice",
    "Keep it short",
    "Write in a warmer tone",
    "The tone is off, make it plainer",
    "Use 'built' instead of 'developed'",
    "Replace 'robust' with 'reliable'",
    "'Synergy' is banned",
    "  Never   say\n leveraged  ",
    "NEVER SAY LEVERAGED",
    "\uff2e\uff45\uff56\uff45\uff52 \uff53\uff41\uff59 leveraged",  # full width letters
    "N'écris jamais « passionné »",
    "n\u2019utilise plus le mot dynamique",
    "Ne mets pas de point d'exclamation",
    "Ne dis jamais leveraged",
    "Évite le jargon",
    "Évite de dire leveraged",
    "Arrête d'écrire synergie",
    "Pas de jargon",
    "Jamais de jargon",
    "Sans jargon, merci",
    "Plus court",
    "Moins formel, plus direct",
    "Le ton est trop formel",  # "ton" after "le" is the tone, not "your"
    "Au lieu de « piloté », écris « mené »",
    "Écris « mené » plutôt que « piloté »",
    "Remplace « piloté » par « mené »",
]

# The refuse table: no offer, and Keep answers 422 with the reason. A fact names the person, holds
# a number, or has a verb that puts words in the document followed by more than quoted words and
# words of style; a shape is a message the code does not recognise as a rule on the wording.
REFUSED = [
    ("Always mention you hold a security clearance", "fact"),
    ("Always say you are fluent in German", "fact"),
    ("Always say you\u2019re a lead", "fact"),
    ("Always say you managed twelve people", "fact"),
    ("Always call me a Staff Engineer", "fact"),
    ("Always mention Kubernetes", "fact"),
    ("Always write PhD after my name", "fact"),
    ("Toujours dire que tu parles allemand", "fact"),
    ("Replace developer with architect", "fact"),
    ("Use built instead of developed", "fact"),
    ("Rather than utilized, say used", "fact"),
    ("Always write the company name in full", "fact"),
    ("Always write twenty-five years", "fact"),
    ("Toujours écrire le nom de l'entreprise en entier", "fact"),
    ("Never say leveraged, say twelve", "fact"),
    ("Never say leveraged. Mention Kubernetes", "fact"),
    ("Avoid buzzwords and mention Kubernetes", "fact"),
    ("Shorter, and highlight Kubernetes", "fact"),
    ("Call the role Staff Engineer", "fact"),
    ("Add a line about Kubernetes", "fact"),
    ("Include the AWS certification", "fact"),
    ("Don't call yourself a rockstar", "fact"),
    ("Never write '24/7'", "fact"),
    ("Can you make it shorter?", "fact"),  # "you": fail closed
    ("We shipped it in March", "fact"),
    ("I led a team of 5 engineers", "fact"),
    ("Never say I have 10 years of Python", "fact"),
    ("Never say I'm junior", "fact"),
    ("Say I'm fluent", "fact"),
    ("Add that I speak German", "fact"),
    ("Mention that I worked at Example Corp", "fact"),
    ("Avoid saying my team was small", "fact"),
    ("My degree is a master, never say bachelor", "fact"),
    ("I'm a staff engineer, never write senior", "fact"),
    ("I also know Kubernetes, avoid saying otherwise", "fact"),
    ("Never write that I managed people", "fact"),
    ("Use 2021 instead of 2020", "fact"),
    ("Use the sidebar template", "fact"),
    ("Put my most relevant experience first", "fact"),
    ("Make it fit on one page", "fact"),
    ("Remplace développeur par architecte", "fact"),
    ("Mentionne Kubernetes", "fact"),
    ("Ajoute le doctorat", "fact"),
    ("Dis que tu as un doctorat", "fact"),
    ("Écris que je parle allemand", "fact"),
    ("Appelle-moi architecte", "fact"),
    ("Toujours écrire Docteur avant mon nom", "fact"),
    ("Ton titre est architecte", "fact"),
    ("Votre diplôme est un master", "fact"),
    ("J'ai dirigé une équipe, évite de le cacher", "fact"),
    ("Ajoute que je parle allemand", "fact"),
    ("Mentionne que j'ai un master", "fact"),
    ("Je parle anglais, évite le français", "fact"),
    ("Mon équipe était de huit personnes, jamais écrire dix", "fact"),
    ("Always say 'fluent in German'", "shape"),  # a quoted phrase added, not swapped
    ("Say 'leveraged' less", "shape"),
    ("Kubernetes experience matters", "shape"),
    ("Never write that the team was small", "shape"),
    ("Avoid saying the company is small", "shape"),
    ("Never write rockstar ninja guru wizard expert", "shape"),
    ("Never say: leveraged", "shape"),
    ("Neversay leveraged", "shape"),
    ("Ne dis pas que la société est petite", "shape"),
    ("Plus de chiffres", "shape"),
    ("Write it again", "shape"),
    ("What does this mean?", "shape"),
    ("Thanks!", "shape"),
    ("", "empty"),
    ("   ", "empty"),
    ("Never say leveraged " * 20, "long"),
]


@pytest.mark.parametrize("text", ACCEPTED)
def test_a_wording_correction_is_offered_as_a_rule(text: str) -> None:
    assert writing_rules.offer(text) == " ".join(text.split())


@pytest.mark.parametrize(("text", "reason"), REFUSED)
def test_no_offer_for_a_fact_or_another_request(text: str, reason: str) -> None:
    assert writing_rules.offer(text) is None
    assert writing_rules.is_fact(text) is (reason == "fact")


@pytest.mark.parametrize("text", ACCEPTED)
def test_keep_keeps_what_the_chat_offers(
    factory: sessionmaker[Session], clock: Any, text: str
) -> None:
    user = make_user(factory, email=f"e2e-rules-{uuid.uuid4().hex[:8]}@example.org", name="Kim")
    give_profile(factory, user.id, clock())
    with factory() as db:
        kept = writing_rules.keep(db, user.id, text, clock())
        db.commit()
    assert kept.rule == " ".join(text.split())
    assert kept.proposal_id is not None
    block = _rules_block(factory, user.id)
    assert block is not None
    assert block["body"].splitlines()[-1] == kept.rule


@pytest.mark.parametrize(("text", "reason"), REFUSED)
def test_keep_refuses_a_fact_and_what_the_chat_would_not_offer(
    factory: sessionmaker[Session], clock: Any, text: str, reason: str
) -> None:
    user = make_user(factory, email=f"e2e-rules-{uuid.uuid4().hex[:8]}@example.org", name="Kim")
    give_profile(factory, user.id, clock())
    with factory() as db:
        with pytest.raises(writing_rules.RuleRefusedError) as refused:
            writing_rules.keep(db, user.id, text, clock())
        db.rollback()
    assert refused.value.reason == reason
    assert _rules_block(factory, user.id) == {"title": "Writing rules", "body": FIRST}


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
