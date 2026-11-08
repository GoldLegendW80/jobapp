"""The posting's own signals, its closing date and its fingerprint (app/jobs/signals.py): pure, in
English and French, with the near misses that must stay silent. Every text is invented."""

from datetime import date

import pytest

from app.apply.answers import EU
from app.jobs import signals
from app.jobs.signals import Signal, closing, fingerprint, place_of, read, same_text, stored


def codes(text: str) -> list[tuple[str, str | None]]:
    return [(s.code, s.value) for s in read(text) if s.code != "closing"]


# --- sponsorship ---------------------------------------------------------------------------------

NO_SPONSORSHIP = [
    "We are unable to offer visa sponsorship for this role.",
    "We do not sponsor visas.",
    "We cannot sponsor work permits at this time.",
    "Unfortunately we can't provide sponsorship.",
    "We are not able to sponsor H-1B visas.",
    "Example Robotics will not sponsor applicants for work visa status.",
    "We are unable to sponsor.",
    "Visa sponsorship is not available for this position.",
    "Sponsorship will not be provided.",
    "Sponsorship is unavailable.",
    "No visa sponsorship.",
    "No sponsorship available.",
    "Candidates must be authorised to work in Canada without sponsorship.",
    "You must be able to work in the US without the need for visa sponsorship now or later.",
    "Applicants must not require sponsorship now or in the future.",
    "Candidates who do not need sponsorship only.",
    "Nous ne sommes pas en mesure de parrainer de visa.",
    "Nous ne pouvons pas sponsoriser de permis de travail.",
    "Nous n’offrons pas de sponsoring visa.",
    "Pas de sponsoring visa possible.",
    "Poste ouvert sans parrainage de visa.",
    "Aucun sponsoring de visa.",
    "Le parrainage de visa n'est pas possible pour ce poste.",
    # The words between the negation and the verb are an allow list.
    "We do not currently sponsor visas.",
    "We will not be able to offer visa sponsorship.",
    "We are not currently able to sponsor work permits.",
    "We cannot at this time sponsor employment visas.",
    "Nous ne pouvons malheureusement pas parrainer de visa.",
    "Il n'est pas possible de prendre en charge votre demande de visa.",
]
SPONSORSHIP = [
    "Visa sponsorship is available.",
    "Visa sponsorship available for the right candidate.",
    "We sponsor visas for international hires.",
    "We can sponsor work permits.",
    "We are happy to support visa applications.",
    "We offer relocation and visa sponsorship.",
    "Benefits include visa support and a relocation package.",
    "Visa sponsorship",
    "Visa sponsorship and relocation support",
    "Relocation and visa support",
    "Sponsoring visa possible.",
    "Accompagnement dans les démarches de visa proposé.",
    "Nous accompagnons votre demande de visa.",
    "Nous pouvons parrainer votre permis de travail.",
]
SPONSORSHIP_SILENT = [
    "We sponsor two hackathons a year.",
    "Don't worry, we sponsor visas.",  # reassured: read as nothing rather than as a refusal
    "Will you now or in the future require sponsorship?",
    "No sponsorship needed for EU citizens.",
    "Our sponsors include Example Labs.",
    "Sponsorship deals with sports teams.",
    "Visa sponsorship for non-EU candidates is available.",  # "non": left alone
    "Le poste ne nécessite pas de visa.",
    "Sans parrainage nécessaire pour les citoyens européens.",
    "Nous parrainons des associations locales.",
    "We do not discriminate on any basis.",
    "Is sponsorship available? Ask us.",
    # "Not just", "can't wait": the negation is not of the sponsoring.
    "We can't wait to sponsor your visa!",
    "We can’t wait to sponsor your visa!",
    "WE CAN'T WAIT TO SPONSOR YOUR VISA!",
    "We don't just sponsor visas, we also cover relocation.",
    "We  don't   just sponsor visas, we also cover relocation.",
    "We do not only offer visa sponsorship but also relocation.",
    "We are not only sponsoring visas but also relocation.",
    "We don't merely sponsor visas.",
    "Nous n'offrons pas seulement un visa mais aussi un logement.",
    "Nous ne proposons pas seulement un visa, mais aussi un logement.",
    "Nous ne parrainons pas que les visas.",
]


@pytest.mark.parametrize("text", NO_SPONSORSHIP)
def test_a_refused_sponsorship_reads_no_sponsorship(text: str) -> None:
    assert ("no_sponsorship", None) in codes(text), text
    assert ("sponsorship", None) not in codes(text), text


@pytest.mark.parametrize("text", SPONSORSHIP)
def test_an_offered_sponsorship_reads_sponsorship(text: str) -> None:
    assert codes(text)[:1] == [("sponsorship", None)], text


@pytest.mark.parametrize("text", SPONSORSHIP_SILENT)
def test_near_misses_say_nothing_of_sponsorship(text: str) -> None:
    found = {code for code, _ in codes(text)}
    assert not found & {"sponsorship", "no_sponsorship"}, text


# --- the right to work ---------------------------------------------------------------------------

WORK_PERMIT = [
    # A requirement stays one: the person provides it, or nobody is named.
    ("Proof of right to work in the UK must be provided.", "United Kingdom"),
    ("A work permit for Germany must be provided by the candidate.", "Germany"),
    ("Le permis de travail pour l'Allemagne doit être fourni par le candidat.", "Germany"),
    ("A work permit for Germany is required, which candidates must obtain.", "Germany"),
    ("Applicants must obtain a work permit for Germany before they start.", "Germany"),
    ("Vous devez obtenir un permis de travail pour l'Allemagne.", "Germany"),
    ("You must have the right to work in the UK.", "United Kingdom"),
    ("Right to work in the United Kingdom", "United Kingdom"),
    ("Applicants must be eligible to work in the EU.", "European Union"),
    ("Candidates must be authorized to work in the United States.", "United States"),
    ("Must be legally authorised to work in the U.S. without sponsorship.", "United States"),
    ("A valid work permit for Switzerland is required.", "Switzerland"),
    ("Valid work authorization in Canada.", "Canada"),
    ("You will need permission to work in Germany.", "Germany"),
    ("Entitled to work in Ireland.", "Ireland"),
    ("Vous devez être autorisé à travailler en France.", "France"),
    ("Autorisation de travail en France requise.", "France"),
    ("Titre de séjour valide en France obligatoire.", "France"),
    ("Permis de travail pour la Suisse indispensable.", "Switzerland"),
    ("Droit de travailler au Royaume-Uni exigé.", "United Kingdom"),
    ("Être autorisée à travailler en Allemagne.", "Germany"),
    (
        "Only candidates with the right to work in the EEA will be considered.",
        "European Economic Area",
    ),
    ("Candidates must be authorised to work in Canada without sponsorship.", "Canada"),
    ("You have the right to work in the UK.", "United Kingdom"),
    ("Proof of the right to work in the UK must be provided.", "United Kingdom"),
    ("Work permit for Germany required.", "Germany"),
    ("WORK PERMIT FOR GERMANY REQUIRED.", "Germany"),
    ("Must  hold a   valid work permit for   Germany.", "Germany"),
    ("We require the right to work in the EU.", "European Union"),
    ("We need you to have the right to work in the UK.", "United Kingdom"),
    ("No sponsorship: you must have the right to work in the UK.", "United Kingdom"),
    ("We cannot sponsor visas, so you must have the right to work in the UK.", "United Kingdom"),
    (
        "Applicants who do not need sponsorship and are authorised to work in the US.",
        "United States",
    ),
    ("Vous disposez d'un permis de travail valide en France.", "France"),
    ("Le candidat doit être autorisé à travailler en France.", "France"),
    ("Permis de travail en Suisse exigé.", "Switzerland"),
    ("You must provide proof of your right to work in the UK.", "United Kingdom"),
    ("We offer a competitive salary; you must have the right to work in the UK.", "United Kingdom"),
    ("We do not provide work permits for the UK; you must already hold one.", "United Kingdom"),
    ("Must be eligible to work in South America.", "South America"),
    ("The successful candidate will provide a valid work permit for the UK.", "United Kingdom"),
    ("You will provide a valid work permit for the UK on day one.", "United Kingdom"),
]
# Every place of a list.
WORK_PERMIT_LISTS = [
    ("Applicants must be eligible to work in the UK or the EU.", ["United Kingdom", EU]),
    ("Must be eligible to work in the UK, Ireland or the EU.", ["United Kingdom", "Ireland", EU]),
    ("You must have the right to work in France and Germany.", ["France", "Germany"]),
    ("Vous devez être autorisé à travailler en France ou en Suisse.", ["France", "Switzerland"]),
]
WORK_PERMIT_SILENT = [
    # The employer gives the right it names, in every shape (review of #762, round 3).
    "A work permit for Germany is required and will be sponsored by the company.",
    "A work permit for Germany is required and is provided by the employer.",
    "Work permit for Germany required (sponsored by the company).",
    "A work permit for Germany is required, which we can provide.",
    "You will need a work permit for Germany, which we arrange.",
    "A work permit in Germany is required, which our HR team obtains for you.",
    "Work permit for Germany required, we arrange it.",
    "A work permit for Germany is required; the company arranges it.",
    "You will need a work permit for Germany; we will arrange it.",
    "You will need a work permit for Germany, and we will handle it for you.",
    "A WORK PERMIT FOR GERMANY IS REQUIRED,  WHICH WE CAN PROVIDE.",
    "A work permit for Germany is required, that the company will arrange.",
    "Un permis de travail pour l'Allemagne est requis et sera pris en charge par l'entreprise.",
    "Un permis de travail pour l'Allemagne est requis, que nous fournissons.",
    "Un permis de travail pour l'Allemagne est requis, nous nous en chargeons.",
    "Un permis de travail pour l'Allemagne est requis, nous le prenons en charge.",
    "Un permis de travail pour l'Allemagne est requis (fourni par l'employeur).",
    "We help you obtain a work permit in Germany.",
    "We support work permit applications for the Netherlands.",
    "A work permit in Spain is not required.",
    "No need for a work permit in France if you are an EU citizen.",
    "Nous vous accompagnons pour obtenir votre titre de séjour en France.",
    "Le permis de travail en France n'est pas requis.",
    "You will work in the UK office.",
    "Right to work in any EU country.",  # no place the rules know
    "Eligible to work in Atlantis.",
    "We value the right to work from home.",
    "Work permit holders welcome.",
    # The employer sponsors, gives, handles or arranges the permit: nobody is asked for it.
    "We will sponsor your visa and work permit in the UK.",
    "WE WILL SPONSOR YOUR VISA AND WORK PERMIT IN THE UK.",
    "We  will   sponsor your visa and work permit in   the UK.",
    "We provide work permit sponsorship for the UK.",
    "We sponsor the work visa in Germany for the successful candidate.",
    "We'll handle your work permit for Germany.",
    "We’ll handle your work permit for Germany.",
    "We can arrange a work permit for the Netherlands.",
    "We will arrange your work permit for Germany; you must relocate to Berlin.",
    "Nous prenons en charge votre permis de travail en France.",
    "NOUS PRENONS EN CHARGE VOTRE PERMIS DE TRAVAIL EN FRANCE.",
    "Nous parrainons votre titre de séjour en France.",
    "Example Robotics will handle your work permit for Germany; you must relocate to Berlin.",
    "Example Robotics can sponsor the work visa in Germany, a valid passport is required.",
    "Nous nous occupons de votre permis de travail en France.",
    "If you don't have the right to work in the UK, we will sponsor you.",
    "Applicants will require a work permit for Germany, which we will sponsor.",
    "Work permit for Germany provided.",
    "A work permit for the UK will be sponsored, valid passport required.",
    "Valid passport required; work permit sponsorship for the UK available.",
    # A negated requirement, or none at all.
    "No work permit for Germany is required.",
    "No visa or work permit for Germany is needed.",
    "A work permit for Germany isn't required.",
    "Aucun permis de travail n'est exigé pour la France.",
    "Pas besoin de permis de travail en France.",
    "Work permit for Germany.",
    "If you need a work permit for Germany, let us know.",
    "Those who need a work permit for Germany can still apply.",
    "You may be eligible for a work permit in Germany through our programme.",
]


@pytest.mark.parametrize(("text", "place"), WORK_PERMIT)
def test_the_right_to_work_names_its_place(text: str, place: str) -> None:
    assert ("work_permit", place) in codes(text), text


@pytest.mark.parametrize(("text", "places"), WORK_PERMIT_LISTS)
def test_the_right_to_work_names_every_place_of_a_list(text: str, places: list[str]) -> None:
    assert [value for code, value in codes(text) if code == "work_permit"] == places


@pytest.mark.parametrize("text", WORK_PERMIT_SILENT)
def test_near_misses_ask_no_right_to_work(text: str) -> None:
    assert all(code != "work_permit" for code, _ in codes(text)), text


def test_a_sentence_can_ask_the_right_to_work_and_refuse_sponsorship() -> None:
    assert codes("Candidates must be authorized to work in the US without sponsorship.") == [
        ("no_sponsorship", None),
        ("work_permit", "United States"),
    ]


@pytest.mark.parametrize(
    ("text", "place"),
    [
        ("UK", "United Kingdom"),
        ("Royaume-Uni", "United Kingdom"),
        ("the United Kingdom", "United Kingdom"),
        ("EU", "European Union"),
        ("European Union (citizen)", "European Union"),
        ("the U.S.", "United States"),
        ("États-Unis", "United States"),
        ("France", "France"),
        ("Allemagne", "Germany"),
        ("", None),
        ("Atlantis", None),
        ("France or Germany", None),
        ("Latin America", "Latin America"),
        ("South America", "South America"),
        ("Remote, Latin America", "Latin America"),
        ("Amérique du Sud", "South America"),
        ("North America", "North America"),
        ("United States of America", "United States"),
        ("America", "United States"),
    ],
)
def test_a_place_reads_as_one_name(text: str, place: str | None) -> None:
    assert place_of(text) == place


# --- degree, contract, pay, fees -----------------------------------------------------------------

POSITIVE = [
    ("degree", "Bachelor's degree in Computer Science required."),
    ("degree", "Master's degree or equivalent."),
    ("degree", "You hold a PhD in physics."),
    ("degree", "A degree in engineering is required."),
    ("degree", "University degree required."),
    ("degree", "Bac+5 exigé."),
    ("degree", "Diplômé(e) d'une école d'ingénieur."),
    ("degree", "Titulaire d'un Master 2 en informatique."),
    ("degree", "Doctorat en chimie requis."),
    ("degree", "Titulaire d'un BTS SIO."),
    ("degree", "BTS ou DUT exigé."),
    ("degree", "Diplômé(e) d'un DUT GEII."),
    ("degree", "DUT Informatique ou équivalent."),
    ("degree", "BTS exigé."),
    ("degree", "Niveau BTS en électronique."),
    ("contractor", "This is an independent contractor role."),
    ("contractor", "1099 contractor position."),
    ("contractor", "Corp-to-corp only."),
    ("contractor", "Mission en freelance de 6 mois."),
    ("contractor", "Statut auto-entrepreneur requis."),
    ("contractor", "Facturation en portage salarial."),
    ("contractor", "Poste en freelance, 3 jours par semaine."),
    ("contractor", "En freelance, à pourvoir rapidement."),
    ("contractor", "Vous travaillerez en freelance depuis Lyon."),
    ("fixed_term", "This is a 12-month fixed-term contract."),
    ("fixed_term", "Temporary contract until June."),
    ("fixed_term", "Six month contract, possible extension."),
    ("fixed_term", "Maternity cover role."),
    ("fixed_term", "CDD de 6 mois."),
    ("fixed_term", "Contrat à durée déterminée."),
    ("fixed_term", "Mission d'intérim de 3 mois."),
    ("fixed_term", "Remplacement d'un congé maternité."),
    ("unpaid", "This is an unpaid internship."),
    ("unpaid", "The role is unpaid."),
    ("unpaid", "Volunteer position, flexible hours."),
    ("unpaid", "Commission only, uncapped."),
    ("unpaid", "Stage non rémunéré."),
    ("unpaid", "Poste bénévole au sein de l'association."),
    ("unpaid", "Mission bénévole de deux jours par semaine."),
    ("candidate_fee", "Candidates must pay a training fee of 300 EUR."),
    ("candidate_fee", "You will need to pay for your own certification."),
    ("candidate_fee", "A registration fee of 50 EUR applies."),
    ("candidate_fee", "A non-refundable training fee is required."),
    ("candidate_fee", "Des frais de formation de 300 euros sont à la charge du candidat."),
    ("candidate_fee", "Formation payante avant la prise de poste."),
    ("candidate_fee", "Le candidat devra payer son matériel."),
]
NEGATIVE = [
    ("degree", "We value a high degree of autonomy."),
    ("degree", "We agree to a degree."),
    ("degree", "A master's degree is a plus."),
    ("degree", "PhD preferred."),
    ("degree", "No degree required."),
    ("degree", "Bac+5 souhaité."),
    ("degree", "You are a Scrum Master's best friend."),
    ("degree", "Master the craft of distributed systems."),
    ("degree", "Notre but en 2026 : grandir."),
    ("degree", "You will debug the DUT (Device Under Test) on the bench."),
    ("degree", "Run regression tests on every DUT."),
    ("degree", "Commission each BTS (Base Transceiver Station) on site."),
    ("degree", "BTS installation and maintenance in the field."),
    ("degree", "Vous testerez le DUT sur banc."),
    ("degree", "Vous mesurerez le niveau de signal de chaque BTS."),
    ("degree", "Le DUT est requis sur le banc de test."),
    ("contractor", "Permanent role, not a contractor basis."),
    ("contractor", "Pas de portage salarial."),
    ("contractor", "You will manage our contractors."),
    ("contractor", "Les auto-entrepreneurs ne sont pas acceptés."),
    ("contractor", "Vous travaillerez avec des développeurs en freelance."),
    ("contractor", "Nous collaborons avec des experts en freelance."),
    ("contractor", "Notre équipe compte des consultants en portage."),
    ("fixed_term", "This is not a fixed-term contract."),
    ("fixed_term", "Permanent role."),
    ("fixed_term", "In the interim, you will lead the team."),
    ("fixed_term", "CDI, pas de CDD."),
    ("fixed_term", "Join for 12 months of growth."),
    ("unpaid", "Unlimited unpaid leave."),
    ("unpaid", "Three paid volunteer days a year."),
    ("unpaid", "Uncapped commission on top of a base salary."),
    ("unpaid", "Stage rémunéré, non logé."),
    ("unpaid", "Une expérience de bénévolat est un plus."),
    ("unpaid", "This is not an unpaid internship."),
    ("candidate_fee", "We pay for your training."),
    ("candidate_fee", "There is no application fee."),
    ("candidate_fee", "Certification fees are covered."),
    ("candidate_fee", "The application fee is waived."),
    ("candidate_fee", "Our placement fee is paid by the employer."),
    ("candidate_fee", "You will pay attention to training details."),
    ("candidate_fee", "Secure upfront payment from clients."),
    ("candidate_fee", "Frais de formation pris en charge."),
    ("candidate_fee", "Les frais de déplacement ne sont pas à la charge du candidat."),
    ("candidate_fee", "Formation gratuite et payante pour nos clients."),
]


@pytest.mark.parametrize(("code", "text"), POSITIVE)
def test_each_signal_reads_its_sentences(code: str, text: str) -> None:
    assert code in {found for found, _ in codes(text)}, text


@pytest.mark.parametrize(("code", "text"), NEGATIVE)
def test_each_signal_leaves_its_near_misses(code: str, text: str) -> None:
    assert code not in {found for found, _ in codes(text)}, text


# Ordinary posting text that must give nothing at all.
SILENT_CORPUS = """Example Robotics builds warehouse robots in Lyon.
You will design perception pipelines with Python and C++.
We offer a hybrid schedule, meal vouchers and 25 days of paid leave.
Our team values autonomy, ownership and a high degree of trust.
You will mentor two engineers and work with product managers.
Nous recherchons un ingénieur logiciel pour notre équipe à Lyon.
Vous travaillerez avec nos équipes produit et data.
Télétravail partiel possible, tickets restaurant, mutuelle prise en charge à 100 %.
Experience with Kubernetes and Terraform is a plus.
Salary: 55,000 to 65,000 EUR a year."""


def test_an_ordinary_posting_says_nothing() -> None:
    assert read(SILENT_CORPUS) == []
    assert read("") == read(None) == []


def test_a_signal_quotes_its_first_sentence_once() -> None:
    text = "Intro.\n- We do not sponsor visas.\nWe cannot sponsor work permits either."
    assert read(text) == [Signal("no_sponsorship", "We do not sponsor visas.")]


def test_a_long_sentence_is_quoted_short() -> None:
    sentence = "We do not sponsor visas " + "and that is final " * 30 + "."
    (found,) = read(sentence)
    assert len(found.quote) == signals.QUOTE_MAX
    assert found.quote.endswith("…")


# --- the closing date ----------------------------------------------------------------------------

CLOSING = [
    ("Closing date: 20 November 2026.", date(2026, 11, 20)),
    ("Applications close on Friday, November 20th, 2026.", date(2026, 11, 20)),
    ("Deadline: 2026-11-20", date(2026, 11, 20)),
    ("Apply by 25/11/2026.", date(2026, 11, 25)),
    ("Apply by 11/25/2026.", date(2026, 11, 25)),
    ("Apply by 11/11/2026.", date(2026, 11, 11)),
    ("Application deadline is 3rd of December 2026.", date(2026, 12, 3)),
    ("Open until Dec 1, 2026.", date(2026, 12, 1)),
    ("Date limite de candidature : 05/12/2026", date(2026, 12, 5)),
    ("Date limite de dépôt des candidatures : le 1er décembre 2026.", date(2026, 12, 1)),
    ("Candidatures jusqu'au 15 janvier 2027.", date(2027, 1, 15)),
    ("Clôture des candidatures le vendredi 20 novembre 2026.", date(2026, 11, 20)),
    ("Postulez avant le 20.11.2026.", date(2026, 11, 20)),
    ("Date de clôture : 2026-11-20", date(2026, 11, 20)),
    ("This role closes on 20 November 2026.", date(2026, 11, 20)),
    ("The posting closes 20 November 2026.", date(2026, 11, 20)),
    ("Closes: 20 November 2026", date(2026, 11, 20)),
    ("Applications are open until 20 November 2026.", date(2026, 11, 20)),
    ("Date limite : 20/11/2026", date(2026, 11, 20)),
]
CLOSING_SILENT = [
    "Apply by 05/06/2026.",  # month and day can be read both ways after an English marker
    "Closing date: 20 November.",  # no year: never guessed
    "Closing date: TBD.",
    "Closing the loop with customers since 2015.",
    "The office closes on Fridays.",
    "Start date: 20 November 2026.",
    "À pourvoir avant le 20 novembre 2026.",
    "Deadline: 31 February 2026.",
    "Published on 20 November 2026.",
    "Deadline: 20 November 2026. Closing date: 27 November 2026.",  # two dates: none
    # "Closes", "deadline" and "open until" of something else than the posting.
    "The office closes on 24 December 2025 for the holidays.",
    "THE OFFICE CLOSES ON 24 DECEMBER 2025.",
    "The cafeteria is closing on 2026-12-24.",
    "Our office is open until 31 December 2026.",
    "The project deadline is 20 November 2026.",
    "La date limite du projet est le 20 novembre 2026.",
    "Le bureau ferme le 24 décembre 2025, clôture annuelle le 31 décembre 2025.",
]


@pytest.mark.parametrize(("text", "day"), CLOSING)
def test_a_closing_date_reads_with_its_sentence(text: str, day: date) -> None:
    assert closing(text) == (day, text)


@pytest.mark.parametrize("text", CLOSING_SILENT)
def test_a_closing_date_is_never_guessed(text: str) -> None:
    assert closing(text) is None, text


def test_the_same_date_twice_is_one_closing_date() -> None:
    text = "Deadline: 20 November 2026.\nApply by 2026-11-20."
    assert closing(text) == (date(2026, 11, 20), "Deadline: 20 November 2026.")


def test_the_closing_date_is_a_signal_and_a_column() -> None:
    text = "We do not sponsor visas.\nClosing date: 20 November 2026."
    assert read(text)[-1] == Signal("closing", "Closing date: 20 November 2026.", "2026-11-20")
    assert stored(text)["closes_on"] == date(2026, 11, 20)
    assert stored(text)["signals"][0] == {
        "code": "no_sponsorship",
        "quote": "We do not sponsor visas.",
        "value": None,
    }


# --- the fingerprint -----------------------------------------------------------------------------

BODY = (
    "You will design and ship the perception stack of our warehouse robots, from camera "
    "calibration to object tracking. You will own the data pipeline that labels millions of "
    "frames, train detection models in PyTorch, and deploy them on embedded GPUs with TensorRT. "
    "You will work with the controls team to close the loop between perception and motion "
    "planning, write design documents, review code, and mentor two junior engineers. We expect "
    "five years of experience with computer vision in production, strong Python and C++, and "
    "a habit of measuring before optimising. The team ships every two weeks and runs its own "
    "on call rotation for the fleet in three warehouses."
)


def test_a_repost_under_another_name_has_the_same_text() -> None:
    direct = f"Example Robotics is hiring.\n{BODY}\nApply on our site."
    agency = f"Our client, a robotics scaleup in Lyon, is hiring.\n{BODY}\nApply on our site."
    assert same_text(fingerprint(direct), fingerprint(agency))
    assert fingerprint(direct) == fingerprint(direct)


def test_another_role_with_shared_boilerplate_is_not_the_same_text() -> None:
    other = (
        "You will lead the payments platform team, owning the ledger, the reconciliation jobs "
        "and the card issuing integration. You will hire three engineers, set the roadmap with "
        "finance, and keep the on call load sane. We expect experience with Go, Postgres and "
        "event sourcing, and a taste for boring, reliable systems that settle money correctly "
        "every night across twelve currencies."
    )
    tail = "\nWe offer a hybrid schedule, meal vouchers and 25 days of paid leave."
    assert not same_text(fingerprint(BODY + tail), fingerprint(other + tail))


def test_a_short_text_has_no_fingerprint_and_is_never_the_same() -> None:
    assert fingerprint("Example Robotics builds warehouse robots.") is None
    assert fingerprint(None) is None
    assert not same_text(None, None)
    value = fingerprint(BODY)
    assert value is not None
    assert -(1 << 63) <= value < 1 << 63
    assert not same_text(value, None)
