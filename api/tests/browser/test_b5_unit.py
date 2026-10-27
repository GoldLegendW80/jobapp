"""The tool layer's rules without a browser (browser B5): the submit and danger detectors as
accept and refuse tables, the host allow list and the refused schemes, the documents placed on a
lease, the snapshot's cap, the pacing bounds, and the lease request's new fields. The tools on a
real Chromium are test_b5_container.py.
"""

import random
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd import detect  # noqa: E402
from browserd.leases import LeaseError, Request  # noqa: E402
from browserd.tools import pacing, policy  # noqa: E402

from tests.browser.case_support import case_spellings  # noqa: E402


def facts(
    *names: str,
    tag: str = "button",
    kind: str = "button",
    role: str = "",
    in_form: bool = True,
    field: str = "",
    form_fields: tuple[str, ...] = (),
    selector_hit: bool = False,
    selector_error: bool = False,
    credential: bool = False,
    **more: Any,
) -> dict[str, Any]:
    """What FACTS_JS answers for an element (`more`: holds_submit, custom_submit, editable)."""
    return {
        "tag": tag,
        "type": kind,
        "role": role,
        "in_form": in_form,
        "names": list(names),
        "selector_hit": selector_hit,
        "selector_error": selector_error,
        "field": field,
        "form_fields": list(form_fields),
        "credential": credential,
        **more,
    }


# --- the submit detector -----------------------------------------------------------------------

SUBMIT_NAMES = [
    "Submit", "SUBMIT", "submit", "Submit application", "Submit Application ", "SubmitApplication",
    "submit_application", "submit-application", "Sub​mit", "ＳＵＢＭＩＴ",
    "Send", "Send application", "Send my application", "Apply", "Apply now", "ApplyNow",
    "apply_now", "Apply for this job", "Postuler", "Je postule", "POSTULEZ", "Envoyer",
    "Envoyer ma candidature", "Envoi de la candidature", "Soumettre", "Soumettre ma candidature",
    "Valider ma candidature", "Confirmer ma candidature", "Confirm application",
    "Finish application", "Finaliser ma candidature", "Terminer ma candidature",
    "Complete application", "  submit  ", "Submit!", "→ Submit", "Submitted? Submit",
    # PR #640 round 3: the French infinitive and imperative, glued, cased and spaced.
    "Candidater", "CANDIDATER", "candidater", "Candidatez", "Candidater maintenant",
    "CandidaterMaintenant", "candidater_maintenant", "Candidatez dès maintenant",
    "→ Candidater", "Ｃａｎｄｉｄａｔｅｒ", "Candi​dater",
]  # fmt: skip


@pytest.mark.parametrize("name", SUBMIT_NAMES)
def test_a_button_named_submit_is_marked(name: str) -> None:
    assert detect.verdict(facts(name)) == "submit"


@pytest.mark.parametrize(
    "shape",
    [
        facts("Submit", tag="div", kind="", role="button"),
        facts("Submit", tag="a", kind=""),
        facts("Envoyer", tag="span", kind=""),
        facts("Postuler", tag="input", kind="button"),
        facts("Go", tag="input", kind="submit"),
        facts("", tag="input", kind="image"),
        facts("Next", tag="button", kind="submit"),  # a <button> without a type in a form
        facts("arrow icon", "Submit", tag="button"),  # the word in an image's alt or aria-label
        facts("Next", selector_hit=True),  # an adapter's own submit selector
        facts("Next", tag="div", kind="", role="link", selector_hit=True),
    ],
)
def test_submit_shapes_are_marked(shape: dict[str, Any]) -> None:
    assert detect.verdict(shape) == "submit"


TRUE_BUTTONS = [
    "Next", "Continue", "Suivant", "Précédent", "Back", "Attach", "Upload file", "Upload resume",
    "Joindre un fichier", "Ajouter un fichier", "Add another", "Yes", "No", "Oui", "Non",
    "Applied AI Engineer", "Application questions", "Resend code", "Pays", "Choose file",
    "Locate me", "Enter manually", "Show more", "Review", "Cancel", "Annuler", "Close",
    "Fermer", "Select country", "Paste", "Order of preference", "LinkedIn profile",
    "Voir l'offre", "Accept cookies", "Tout accepter", "Edit", "Modifier",
    # Neighbours of Candidater and Contacter le recruteur that stay ordinary.
    "Candidat", "Espace candidat", "Candidate portal", "Candidates", "Candidature",
    "Mes candidatures", "Candidature spontanée", "Candidate login", "Contact", "Contact us",
    "Contactez-nous", "Nous contacter", "Contacts", "Recruteur", "Our recruiters",
    "Recruiting process", "Recrutement", "Messages", "Hiring process", "Employer branding",
    "Employeur", "Écrire", "Contact details",
]  # fmt: skip


@pytest.mark.parametrize("name", TRUE_BUTTONS)
def test_ordinary_buttons_are_not_marked(name: str) -> None:
    assert detect.verdict(facts(name)) is None


@pytest.mark.parametrize(
    ("shape", "action"),
    [
        # A consent box or a field says submit or delete in its label: ticked and typed.
        (facts("I confirm what I submit is true", tag="input", kind="checkbox"), "check"),
        (facts("You may ask for the deletion of your data", tag="input", kind="checkbox"), "check"),
        (facts("Send me job alerts", tag="input", kind="checkbox"), "check"),
        (facts("Why do you want to apply?", tag="textarea", kind="textarea"), "type"),
        (facts("Why do you want to apply?", tag="textarea", kind="textarea"), "click"),
        (facts("Pays", tag="select", kind="select-one"), "check"),
        (facts("Visa expiry date", tag="input", kind="text", field="visa_expiry"), "type"),
        (facts("Carte de séjour", tag="input", kind="text", field="carte de séjour"), "type"),
        (facts("Security code", tag="input", kind="text", field="security_code"), "type"),
        (facts("Apply", tag="input", kind="text", field="apply"), "type"),
        (facts("Remove spaces", tag="input", kind="text"), "click"),
    ],
)  # fmt: skip
def test_fields_and_boxes_are_never_read_as_buttons(shape: dict[str, Any], action: str) -> None:
    assert detect.verdict(shape, action=action) is None


def test_the_profile_purpose_adds_save_and_update() -> None:
    for name in ("Save", "Save changes", "Update profile", "Enregistrer", "Sauvegarder",
                 "Mettre à jour", "Mise à jour du profil"):  # fmt: skip
        assert detect.verdict(facts(name), profile_edit=True) == "submit", name
        assert detect.verdict(facts(name)) is None, name


# --- the danger detector -----------------------------------------------------------------------

DANGER_NAMES = [
    "Delete", "Delete account", "Remove", "Remove file", "Supprimer", "Suppression du compte",
    "Effacer", "Withdraw", "Withdraw application", "Retirer ma candidature", "Pay", "Pay now",
    "Payer", "Paiement", "Payment", "Buy", "Acheter", "Purchase", "Checkout",
    "Cancel application", "Annuler ma candidature", "Unsubscribe", "Se désinscrire",
    "Deactivate account", "Désactiver le compte", "Commander", "Pay with PayPal",
    # PR #640 round 3: writing to a recruiter, in French and English, glued and cased.
    "Contacter le recruteur", "CONTACTER LE RECRUTEUR", "Contactez le recruteur",
    "Contacter la recruteuse", "Contacter l'employeur", "Contacter le recruteur ✉",
    "ContacterLeRecruteur", "contacter_le_recruteur", "Contact recruiter", "Contact the recruiter",
    "Contact the hiring manager", "Contact employer", "Message the recruiter", "Message recruiter",
    "Envoyer un message au recruteur", "Écrire au recruteur", "Écrivez au recruteur",
    "Contacter le recrutement",
]  # fmt: skip


@pytest.mark.parametrize("name", DANGER_NAMES)
def test_a_dangerous_button_is_refused_even_by_the_gate(name: str) -> None:
    assert detect.verdict(facts(name)) == "danger"


@pytest.mark.parametrize(
    "field",
    ["cc-number", "cc-csc", "cc-exp", "cardNumber", "card_number", "CVC", "cvv", "Credit card",
     "Numéro de carte", "numero de la carte", "Carte bancaire", "Cryptogramme visuel"],
)  # fmt: skip
def test_card_fields_are_refused(field: str) -> None:
    card = facts("", tag="input", kind="text", field=field)
    assert detect.verdict(card, action="type") == "card_field"
    assert detect.verdict(card, action="click") == "card_field"
    # A button in a form that holds a card field is refused too.
    assert detect.verdict(facts("Next", form_fields=("email", field))) == "card_field"


# --- case spellings (B17 follow up 3) ----------------------------------------------------------

# The detector is shared by every lease, the apply leases included: a name refused in lower case is
# refused in each of its case spellings (case_support), so `SubMit`, `AppLy` or `DeLete` reads as
# the word it spells (detect.match_words). Generated from the tables above. A glued verb and noun
# written in one case is one word, so the names whose lower case is itself not refused rest on
# their camel case split and are listed here (declared: `contacterlerecruteur` is not read).
CAMEL_ONLY = ["ContacterLeRecruteur"]


@pytest.mark.parametrize("name", SUBMIT_NAMES)
def test_every_case_spelling_of_a_submit_name_is_marked(name: str) -> None:
    for spelling in case_spellings(name):
        assert detect.verdict(facts(spelling)) == "submit", spelling


@pytest.mark.parametrize("name", [n for n in DANGER_NAMES if n not in CAMEL_ONLY])
def test_every_case_spelling_of_a_dangerous_name_is_refused(name: str) -> None:
    for spelling in case_spellings(name):
        assert detect.verdict(facts(spelling)) == "danger", spelling


def test_every_case_spelling_of_save_and_update_is_marked_for_the_profile() -> None:
    for name in ("Save", "Save changes", "Update profile", "Enregistrer", "Sauvegarder",
                 "Mettre à jour", "Mise à jour du profil"):  # fmt: skip
        for spelling in case_spellings(name):
            assert detect.verdict(facts(spelling), profile_edit=True) == "submit", spelling


def test_only_the_listed_names_rest_on_their_camel_case_split() -> None:
    names = [*SUBMIT_NAMES, *DANGER_NAMES]
    assert [n for n in names if detect.verdict(facts(n.lower())) is None] == CAMEL_ONLY


# The reviewer's rows of B17 round 3, never deleted: False on cf91a8a0 and 4b547a0a.
@pytest.mark.parametrize(
    ("name", "submit"),
    [("SubMit", True), ("sUbmit", True), ("AppLy", True), ("ApPly", True), ("Submit", True),
     ("SUBMIT", True), ("Apply now", True), ("Applied AI Engineer", False)],
)  # fmt: skip
def test_round3_mixed_case_rows(name: str, submit: bool) -> None:
    assert detect.says_submit([name]) is submit
    assert (detect.verdict(facts(name)) == "submit") is submit


@pytest.mark.parametrize("name", TRUE_BUTTONS)
def test_an_ordinary_button_written_in_one_case_stays_ordinary(name: str) -> None:
    for spelling in {name.lower(), name.upper(), name.title(), name.capitalize()}:
        assert detect.verdict(facts(spelling)) is None, spelling
        assert detect.match_words(spelling) == detect.words(spelling), spelling


# --- keys, selectors, unreadable -------------------------------------------------------------


def test_enter_in_a_field_of_a_form_is_a_submit() -> None:
    field = facts("Email", tag="input", kind="email", field="email")
    assert detect.verdict(field, action="key") == "submit"
    assert detect.verdict({**field, "in_form": False}, action="key") is None  # declared open
    textarea = facts("Cover letter", tag="textarea", kind="textarea")
    assert detect.verdict(textarea, action="key") is None
    assert detect.verdict(facts("Submit"), action="key") == "submit"
    assert detect.verdict(facts("Next"), action="key") is None


def test_a_bad_selector_and_an_unreadable_element_fail_closed() -> None:
    assert detect.verdict(facts("Next", selector_error=True)) == "bad_selector"
    for broken in (None, {}, {"names": "Submit"}, []):
        assert detect.verdict(broken) == "unreadable"


# --- the click point, containers, shadow roots, typing (review of PR #297) ----------------------

SUBMIT_BUTTON = facts("Continue", kind="submit")
CONTAINERS = [
    facts("Continue", tag="div", kind="", holds_submit=True),
    facts("Application", tag="form", kind="", holds_submit=True),
    facts("Your details", tag="fieldset", kind="", holds_submit=True),
    facts("", tag="section", kind="", in_form=False, holds_submit=True),
    facts("Card", tag="li", kind="", holds_submit=True),
]


@pytest.mark.parametrize("wrapper", CONTAINERS)
def test_a_container_holding_a_submit_is_marked(wrapper: dict[str, Any]) -> None:
    for action in ("click", "check", "key"):
        assert detect.verdict(wrapper, action=action) == "submit"
    no_submit = {**wrapper, "holds_submit": False}
    assert detect.verdict(no_submit) is None
    # Its click point is the submit: marked by the point too.
    assert detect.verdict({**no_submit, "point": {**SUBMIT_BUTTON, "inside": True}}) == "submit"


@pytest.mark.parametrize(
    "point",
    [
        {**facts("Continue", kind="submit"), "inside": False},
        {**facts("Delete", kind="button"), "inside": True},
        {**facts("", tag="span", kind="", selector_hit=True), "inside": True},
        {**facts("Go", tag="sl-button", kind="submit", custom_submit=True), "inside": False},
        {**facts("Next", tag="button", kind="button", form_fields=("cc-number",)), "inside": False},
        None,
        "button",
        {},
    ],
    ids=["submit over it", "delete inside", "lease selector", "sl-button", "card form", "none",
         "not facts", "empty"],
)  # fmt: skip
def test_the_element_at_the_click_point_is_judged(point: Any) -> None:
    target = facts("Next", tag="div", kind="")
    assert detect.verdict({**target, "point": point}, action="click") is not None
    assert detect.verdict({**target, "point": point}, action="check") is not None


def test_a_point_inside_a_text_field_lets_it_type_and_one_elsewhere_is_judged() -> None:
    field = facts("Email", tag="input", kind="email", field="email")
    assert detect.verdict({**field, "point": {**field, "inside": True}}, action="type") is None
    covered = {**field, "point": {**SUBMIT_BUTTON, "inside": False}}
    assert detect.verdict(covered, action="type") == "submit"
    assert detect.verdict({**field, "point": None}, action="type") == "unreadable"
    # Enter (key) does not click: the point is not read for it.
    assert detect.verdict({**field, "point": None, "in_form": False}, action="key") is None


@pytest.mark.parametrize(
    "shape",
    [
        # A span in a shadow root under a submit button: FACTS_JS walks up across the shadow
        # root, so the control read is the button.
        facts("", tag="button", kind="submit", in_form=True),
        # <sl-button type="submit">: a form associated custom element, or the element inside its
        # shadow root (custom_submit is set by the walk over the composed ancestors).
        facts("Continue", tag="sl-button", kind="submit", custom_submit=True),
        facts("Continue", tag="button", kind="button", custom_submit=True),
        facts("Continue", tag="md-filled-button", kind="image", custom_submit=True),
    ],
)
def test_shadow_roots_and_custom_submit_elements_are_marked(shape: dict[str, Any]) -> None:
    for action in ("click", "key", "check", "type"):
        assert detect.verdict(shape, action=action) in ("submit", "not_editable"), action
    assert detect.verdict(shape) == "submit"


@pytest.mark.parametrize(
    ("shape", "entry"),
    [
        (facts("Email", tag="input", kind="email"), True),
        (facts("", tag="input", kind=""), True),
        (facts("When", tag="input", kind="datetime-local"), True),
        (facts("Pw", tag="input", kind="password"), True),
        (facts("Letter", tag="textarea", kind="textarea"), True),
        (facts("Letter", tag="div", kind="", editable=True), True),
        (facts("Letter", tag="p", kind="", editable=True), True),
        (facts("City", tag="div", kind="", role="combobox"), True),
        (facts("Q", tag="span", kind="", role="searchbox"), True),
        (facts("Name", tag="div", kind="", role="textbox"), True),
        (facts("Submit", tag="input", kind="submit"), False),
        (facts("Go", tag="input", kind="image"), False),
        (facts("Go", tag="input", kind="button"), False),
        (facts("Reset", tag="input", kind="reset"), False),
        (facts("Yes", tag="input", kind="checkbox"), False),
        (facts("Yes", tag="input", kind="radio"), False),
        (facts("CV", tag="input", kind="file"), False),
        (facts("", tag="input", kind="hidden"), False),
        (facts("Color", tag="input", kind="color"), False),
        (facts("Level", tag="input", kind="range"), False),
        (facts("Weird", tag="input", kind="unknown-kind"), False),
        (facts("Next", tag="button", kind="button"), False),
        (facts("Next", tag="button", kind="button", editable=True), False),
        (facts("Link", tag="a", kind=""), False),
        (facts("Link", tag="a", kind="", role="textbox"), False),
        (facts("Pays", tag="select", kind="select-one"), False),
        (facts("Email", tag="label", kind=""), False),
        (facts("Frame", tag="iframe", kind=""), False),
        (facts("Box", tag="div", kind=""), False),
        (facts("Box", tag="div", kind="", role="button"), False),
    ],
)  # fmt: skip
def test_typing_goes_into_a_text_entry_only(shape: dict[str, Any], entry: bool) -> None:
    assert detect.text_entry(shape) is entry
    assert detect.verdict(shape, action="type") == (None if entry else "not_editable")


def test_huge_names_stay_bounded() -> None:
    noise = ["x" * 100_000] * 1000
    assert detect.verdict(facts(*noise)) is None
    assert detect.verdict(facts("Submit", *noise)) == "submit"


# --- hosts and schemes ---------------------------------------------------------------------------

HOSTS = ("boards.greenhouse.io", "*.ashbyhq.com", "pages")


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/examplelabs/jobs/1",
        "HTTPS://BOARDS.GREENHOUSE.IO/x",
        "https://boards.greenhouse.io./x",
        "https://boards.greenhouse.io:443/x?gh_src=1#app",
        "http://pages:8000/b5/form.html",
        "https://jobs.ashbyhq.com/example/123/application",
        "https://a.b.ashbyhq.com/",
    ],
)
def test_urls_of_the_list_are_opened(url: str) -> None:
    assert policy.url_verdict(url, HOSTS) is None


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://ashbyhq.com/", "host_not_allowed"),
        ("https://evilashbyhq.com/", "host_not_allowed"),
        ("https://boards.greenhouse.io.evil.example/", "host_not_allowed"),
        ("https://greenhouse.io/", "host_not_allowed"),
        ("https://job-boards.greenhouse.io/", "host_not_allowed"),
        ("https://evil.example%40boards.greenhouse.io/", "host_not_allowed"),
        ("https://[::1]/", "host_not_allowed"),
        ("https://127.0.0.1/", "host_not_allowed"),
        ("http://pages.evil.example/", "host_not_allowed"),
        ("https://boards.greenhouse.io@evil.example/", "bad_url"),
        ("https://user:pw@boards.greenhouse.io/", "bad_url"),
        ("https://evil.example\\@boards.greenhouse.io/", "bad_url"),
        ("https://bоards.greenhouse.io/", "bad_url"),
        (" https://boards.greenhouse.io/", "bad_url"),
        ("https://boards.greenhouse.io/\tx", "bad_url"),
        ("https://boards.greenhouse.io:99999/", "bad_url"),
        ("https://" + "a" * 3000, "bad_url"),
        ("", "bad_url"),
        (None, "bad_url"),
        ("file:///etc/passwd", "refused_scheme"),
        ("FILE:///etc/passwd", "refused_scheme"),
        ("chrome://settings", "refused_scheme"),
        ("chrome-extension://abc/x.html", "refused_scheme"),
        ("devtools://devtools/bundled/inspector.html", "refused_scheme"),
        ("view-source:https://boards.greenhouse.io/", "refused_scheme"),
        ("data:text/html,<h1>x</h1>", "refused_scheme"),
        ("javascript:alert(1)", "refused_scheme"),
        ("blob:https://boards.greenhouse.io/1", "refused_scheme"),
        ("about:blank", "refused_scheme"),
        ("ftp://boards.greenhouse.io/", "refused_scheme"),
        ("//boards.greenhouse.io/x", "refused_scheme"),
        ("ws://boards.greenhouse.io/", "refused_scheme"),
    ],
)
def test_urls_off_the_list_are_refused(url: Any, code: str) -> None:
    assert policy.url_verdict(url, HOSTS) == code


def test_an_empty_list_opens_nothing() -> None:
    assert policy.url_verdict("https://boards.greenhouse.io/", ()) == "host_not_allowed"


@pytest.mark.parametrize(
    "raw",
    [["*.com"], ["*"], ["http://x.example"], ["a b.example"], ["10.0.0.1"], [1], ["*.*.x.com"],
     ["**.x.com"], ["x.example/"], ["-x.example"], ["x" * 64 + ".example"], ["a.example"] * 21,
     "boards.greenhouse.io", {"a": 1}],
)  # fmt: skip
def test_bad_host_entries_refuse_the_lease(raw: Any) -> None:
    with pytest.raises(ValueError, match="hosts"):
        policy.parse_hosts(raw)


def test_host_entries_are_folded() -> None:
    assert policy.parse_hosts(["Boards.Greenhouse.IO.", "*.AshbyHQ.com", "pages", "pages"]) == (
        "boards.greenhouse.io",
        "*.ashbyhq.com",
        "pages",
    )


def test_the_lease_request_carries_hosts_and_selectors() -> None:
    body = {
        "user_id": "u-1",
        "profile_id": "6f1c2a52-6c55-4a0a-9a3f-0d6c1f6d2b11",
        "uid": 20001,
        "purpose": "apply_fill",
        "hosts": ["pages"],
        "submit_selectors": ["#submit_app"],
    }
    request = Request.parse(body)
    assert request.hosts == ("pages",)
    assert request.submit_selectors == ("#submit_app",)
    assert Request.parse({k: v for k, v in body.items() if k != "hosts"}).hosts == ()
    with pytest.raises(LeaseError) as bad:
        Request.parse({**body, "hosts": ["*"]})
    assert bad.value.code == "bad_hosts"
    with pytest.raises(LeaseError) as bad:
        Request.parse({**body, "submit_selectors": ["x" * 201]})
    assert bad.value.code == "bad_submit_selectors"


# --- documents, snapshot, refs, pacing -------------------------------------------------------

PDF = b"%PDF-1.7\n" + b"0" * 100


@pytest.mark.parametrize(
    ("name", "data", "held", "code"),
    [
        ("cv.pdf", PDF, {}, None),
        ("Lucien_Quillard-CV.v2.pdf", PDF, {}, None),
        ("cv.pdf", b"%PDF-" + b"0" * (policy.FILE_MAX - 5), {}, None),
        ("cv.pdf", b"%PDF-" + b"0" * (policy.FILE_MAX - 4), {}, "file_too_large"),
        ("cv.pdf", b"PK\x03\x04", {}, "not_a_pdf"),
        ("cv.pdf", b" %PDF-1.7", {}, "not_a_pdf"),
        ("cv.pdf", b"", {}, "not_a_pdf"),
        ("../cv.pdf", PDF, {}, "bad_file_name"),
        ("cv.PDF", PDF, {}, "bad_file_name"),
        ("cv.pdf.exe", PDF, {}, "bad_file_name"),
        (".cv.pdf", PDF, {}, "bad_file_name"),
        ("cv one.pdf", PDF, {}, "bad_file_name"),
        ("cé.pdf", PDF, {}, "bad_file_name"),
        ("x" * 80 + ".pdf", PDF, {}, "bad_file_name"),
        ("d.pdf", PDF, {"a.pdf": PDF, "b.pdf": PDF, "c.pdf": PDF}, "too_many_files"),
        ("c.pdf", PDF, {"a.pdf": PDF, "b.pdf": PDF, "c.pdf": PDF}, None),
    ],
)
def test_documents_placed_on_a_lease(name: str, data: bytes, held: dict, code: str | None) -> None:
    assert policy.file_verdict(name, data, held) == code


def test_a_snapshot_past_the_cap_is_cut_with_a_marker() -> None:
    small = '- button "Next" [ref=e1]\n'
    assert policy.cut_snapshot(small) == (small, False)
    big = "".join(f'- textbox "Field {i}" [ref=e{i}]\n' for i in range(5000))
    text, cut = policy.cut_snapshot(big)
    assert cut
    assert len(text) <= policy.SNAPSHOT_MAX
    assert text.endswith("more not shown]")
    assert "snapshot cut at 40000 characters" in text
    body = text[: text.rindex("\n[snapshot cut")]
    assert big.startswith(body)
    assert body.endswith("]")  # cut at a line end, never inside a ref


@pytest.mark.parametrize("ref", ["e1", "e1234567", "f1e5"])
def test_refs_of_the_snapshot_are_read(ref: str) -> None:
    assert policy.ref_of({"ref": ref}) == ref


@pytest.mark.parametrize(
    "ref", ["", "e", "1", "E1", "e1 ", "#submit", "e1,e2", None, 1, "e12345678"]
)
def test_anything_else_is_not_a_ref(ref: Any) -> None:
    with pytest.raises(ValueError, match="ref"):
        policy.ref_of({"ref": ref})


def test_pacing_stays_within_its_bounds() -> None:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    pacer = pacing.Pacer(sleep, random.Random(5))  # noqa: S311 - a seeded source for the test
    keys = pacer.key_delays_s(2000)
    assert all(0.060 <= d <= 0.140 for d in keys)
    assert min(keys) < 0.065
    assert max(keys) > 0.135  # the whole range is used, not a constant

    import asyncio

    async def run() -> tuple[list[float], list[float], list[float]]:
        dwell = [await pacer.dwell() for _ in range(500)]
        hover = [await pacer.hover() for _ in range(500)]
        read = [await pacer.read() for _ in range(500)]
        return dwell, hover, read

    dwell, hover, read = asyncio.run(run())
    assert all(1.0 <= d <= 3.0 for d in dwell)
    assert min(dwell) < 1.1
    assert max(dwell) > 2.9
    assert all(0.2 <= d <= 0.6 for d in hover)
    assert all(1.5 <= d <= 4.0 for d in read)
    assert slept == dwell + hover + read  # every pause is slept, through the injected sleep
