"""Opportunities O3: the pay and the work mode of a posting (tasks/opportunities/todo.md, O3).

The accept and refuse table of app/opportunities/pay.py (30 to 40 neighbours around each shape of
the allow list), the work mode of a location, what Ashby and Lever state, the sweep filling the
columns, the migration's check, PATCH keeping the person's value through a rescore and a new
sweep, the deck's card and the posting's detail, and isolation between two people. Everything is
invented: no real company, person or site.
"""

from __future__ import annotations

import uuid
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import ats
from app.jobs.models import Job
from app.opportunities.pay import Pay, pay_of, pay_text, pays, stated_pay, work_mode_of
from tests.identity.api_helpers import Api
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import board, new_sweep, run, set_profile
from tests.opportunities.conftest import Person, add_posting, deck_search, get_deck

NBSP, NNBSP, THIN = " ", " ", " "
CSRF = {"X-Requested-With": "jobapp"}
TZ = ZoneInfo("Europe/Paris")


def y(low: int, high: int, currency: str = "EUR", period: str = "year") -> Pay:
    return Pay(min=low, max=high, currency=currency, period=period)


def one(figure: int, currency: str = "EUR", period: str = "day") -> Pay:
    return Pay(min=figure, max=figure, currency=currency, period=period)


# --- the accept and refuse table ----------------------------------------------------------------

# "60,000 to 70,000 EUR a year": a range with a word, the code after, a period after.
RANGE_WORDS = [
    ("60,000 to 70,000 EUR a year", y(60000, 70000)),
    ("60,000 to 70,000 EUR per year", y(60000, 70000)),
    ("60,000 to 70,000 EUR/year", y(60000, 70000)),
    ("60,000 to 70,000 EUR / year", y(60000, 70000)),
    ("60,000 to 70,000 EUR per annum", y(60000, 70000)),
    ("60,000 to 70,000 EUR p.a.", y(60000, 70000)),
    ("60,000 to 70,000 EUR annually", y(60000, 70000)),
    ("60,000 to 70,000 EUR gross a year", y(60000, 70000)),
    ("60.000 to 70.000 EUR a year", y(60000, 70000)),
    ("60 000 to 70 000 EUR a year", y(60000, 70000)),
    (f"60{NBSP}000 to 70{NBSP}000 EUR a year", y(60000, 70000)),
    (f"60{NNBSP}000 to 70{NNBSP}000 EUR a year", y(60000, 70000)),
    (f"60{THIN}000 to 70{THIN}000 EUR a year", y(60000, 70000)),
    ("60000 to 70000 EUR a year", y(60000, 70000)),
    ("60,000 TO 70,000 EUR A YEAR", y(60000, 70000)),
    ("Salary: 60,000 to 70,000 EUR a year.", y(60000, 70000)),
    ("between 60,000 and 70,000 EUR a year", y(60000, 70000)),
    ("entre 60 000 et 70 000 EUR par an", y(60000, 70000)),
    ("from 60,000 to 70,000 EUR a year", y(60000, 70000)),
    ("60,000 to 70,000 GBP a year", y(60000, 70000, "GBP")),
    ("60,000 to 70,000 CHF a year", y(60000, 70000, "CHF")),
    ("EUR 60,000 to EUR 70,000 a year", y(60000, 70000)),
    ("60,000 EUR to 70,000 EUR a year", y(60000, 70000)),
    ("4,000 to 5,000 EUR a month", y(4000, 5000, period="month")),
    ("60,000 to 70,000 EUR", None),  # no period
    ("60,000 to 70,000 a year", None),  # no currency
    ("60,000 to 70,000 eur a year", None),  # a code is written in capitals
    ("60,000 to 70,000 EUR a week", None),  # not a period of the list
    ("60,000 to 70,000 EUR a yearly", None),  # "a" then an adjective
    ("70,000 to 60,000 EUR a year", None),  # reversed
    ("60,000 to 70,000 XYZ a year", None),  # unknown code
    ("60,000 to 70,000 EUR a year to 80,000 EUR", y(60000, 70000)),
    ("60,000 to 70,000 USD per month per year", y(60000, 70000, "USD", "month")),
    ("60,000 to 70,000 EURO a year", y(60000, 70000)),
    ("60,000 to 70,000 euros par an", y(60000, 70000)),
    ("60,000 to 70,000 EUR a yearbook", None),
]

# "60k-70k €" read as a range: "k", a hyphen or a dash, the sign; a period is still needed.
RANGE_K = [
    ("60k-70k € per year", y(60000, 70000)),
    ("60k - 70k € per year", y(60000, 70000)),
    ("60K-70K € per year", y(60000, 70000)),
    ("60k–70k € per year", y(60000, 70000)),
    ("60k—70k € per year", y(60000, 70000)),
    ("60k~70k € per year", y(60000, 70000)),
    ("60-70k€ per year", y(60000, 70000)),
    ("60-70k € per year", y(60000, 70000)),
    ("60-70 k€ per year", y(60000, 70000)),
    ("60k€-70k€ per year", y(60000, 70000)),
    ("€60k-€70k per year", y(60000, 70000)),
    ("€60k–€70k a year", y(60000, 70000)),
    ("60k-70k€/an", y(60000, 70000)),
    ("60k-70k€ brut annuel", y(60000, 70000)),
    ("60k-70k€ annuel", y(60000, 70000)),
    ("60k-70kEUR per year", y(60000, 70000)),
    ("60,5k-70k € per year", y(60500, 70000)),
    ("1,5k-2k € par mois", y(1500, 2000, period="month")),
    ("60k-70k £ a year", y(60000, 70000, "GBP")),
    ("$60k-$70k a year", y(60000, 70000, "USD")),
    ("60k-70k €", None),  # no period
    ("60k-70k", None),  # no currency, no period
    ("60k-70k € par semaine", None),
    ("70k-60k € per year", None),  # reversed
    ("60k-70k km per year", None),  # "km" is no thousands, no currency
    ("60k-70k €€ per year", None),
    ("60k-70k € per year", y(60000, 70000)),
    ("-60k-70k € per year", y(60000, 70000)),
    ("2025-2026 € per year", y(2025, 2026)),  # a statement in the shape, read as written
    ("2026-10 € per year", None),  # reversed
    ("60k-70k € et 0,1% equity per year", None),  # the period is not the figures
    ("60k-70k$ per year", y(60000, 70000, "USD")),
    ("60k-70k US$ per year", y(60000, 70000, "USD")),
    ("60k-70k CA$ per year", y(60000, 70000, "CAD")),
    ("60k-70k C$ a year", y(60000, 70000, "CAD")),
    ("60k-70k € 2 days a week", None),
]

# "€600/day" and "TJM 600 €": one daily figure, the sign before or after, the period after or
# named before.
DAILY = [
    ("€600/day", one(600)),
    ("€600 / day", one(600)),
    ("€ 600/day", one(600)),
    ("€600 per day", one(600)),
    ("€600 a day", one(600)),
    ("€600 daily", one(600)),
    ("600€/jour", one(600)),
    ("600 €/j", one(600)),
    (f"600{NBSP}€ par jour", one(600)),
    ("600 € HT/jour", one(600)),
    ("600 € HT par jour", one(600)),
    ("600 € journalier", one(600)),
    ("£500/day", one(500, "GBP")),
    ("$700/day", one(700, "USD")),
    ("€600/DAY", one(600)),
    ("TJM 600 €", one(600)),
    ("TJM : 600 €", one(600)),
    ("TJM: 600€", one(600)),
    ("TJM de 600 €", one(600)),
    ("tjm 600 €", one(600)),
    ("TJM 600 € HT", one(600)),
    ("TJM 500 à 650 €", y(500, 650, period="day")),
    ("Taux journalier moyen : 600 €", one(600)),
    ("Daily rate: €600", one(600)),
    ("Day rate of £550", one(550, "GBP")),
    ("TJM 600 € par an", None),  # two periods
    ("TJM 600", None),  # no currency
    ("TJM selon profil", None),
    ("€600", None),  # no period
    ("€600/week", None),
    ("€600/days off", None),
    ("€600/dayz", None),
    ("€0.50/day", None),  # under 1
    ("€0/day", None),
    ("€600.50/day", None),  # not whole
    ("€600,00/day", one(600)),
    ("up to €600/day", None),  # an open bound on one figure
    ("from €600/day", None),
    ("à partir de 600 €/jour", None),
    ("jusqu’à 600 €/jour", None),
    ("minimum 600 €/jour", None),
]

# "500 à 650 € par jour": a French range, the sign after, the period after.
FRENCH_RANGE = [
    ("500 à 650 € par jour", y(500, 650, period="day")),
    ("500 € à 650 € par jour", y(500, 650, period="day")),
    ("de 500 à 650 € par jour", y(500, 650, period="day")),
    ("entre 500 et 650 € par jour", y(500, 650, period="day")),
    ("500 à 650 euros par jour", y(500, 650, period="day")),
    ("500 à 650 € / jour", y(500, 650, period="day")),
    ("500 à 650€/j", y(500, 650, period="day")),
    ("500-650 € par jour", y(500, 650, period="day")),
    ("500 à 650 € HT par jour", y(500, 650, period="day")),
    ("500 à 650 € par mois", y(500, 650, period="month")),
    ("500 à 650 € de l'heure", y(500, 650, period="hour")),
    ("500 à 650 € de l’heure", y(500, 650, period="hour")),
    ("50 à 65 € par heure", y(50, 65, period="hour")),
    ("50 à 65 €/h", y(50, 65, period="hour")),
    ("50 à 65 € horaire", y(50, 65, period="hour")),
    ("500 À 650 € PAR JOUR", y(500, 650, period="day")),
    ("500  à  650  €  par  jour", y(500, 650, period="day")),
    ("500 à 650 €", None),
    ("500 à 650 par jour", None),
    ("650 à 500 € par jour", None),
    ("500 à 650 € par semaine", None),
    ("500 à 650 $ par jour", y(500, 650, "USD", "day")),
    ("500 à 650 € et £ par jour", None),
    ("500 € à 650 £ par jour", None),  # two currencies in one statement
    ("prime de 500 à 650 € par jour", None),  # a bonus
    ("Prime : 500 à 650 € par jour", None),
    ("500 à 650 € par jour ouvré", y(500, 650, period="day")),
    ("500 à 650 € par journée", y(500, 650, period="day")),
    ("500 à 650 € par jours", None),
    ("05 00 650 € par jour", None),  # inside a run of digits
    ("500 à 650 % par jour", None),
    ("500% à 650 € par jour", one(650)),  # the percentage is no figure
    ("1 500 à 1 650 € par jour", y(1500, 1650, period="day")),
    ("500,00 à 650,00 € par jour", y(500, 650, period="day")),
    ("500.5 à 650 € par jour", None),
]

# "45 K€ brut annuel": a French yearly figure, "K" then the sign, words after.
FRENCH_YEAR = [
    ("45 K€ brut annuel", one(45000, period="year")),
    ("45K€ brut annuel", one(45000, period="year")),
    ("45k€ brut annuel", one(45000, period="year")),
    ("45 k€ brut annuel", one(45000, period="year")),
    ("45 K€ annuel", one(45000, period="year")),
    ("45 K€ annuels", one(45000, period="year")),
    ("45 K€ par an", one(45000, period="year")),
    ("45 K€/an", one(45000, period="year")),
    ("45 K€ brut / an", one(45000, period="year")),
    ("45 K€ bruts annuels", one(45000, period="year")),
    ("45 000 € brut annuel", one(45000, period="year")),
    ("45.000 € brut annuel", one(45000, period="year")),
    (f"45{NBSP}000{NBSP}€ brut annuel", one(45000, period="year")),
    ("45 K€ brut mensuel", one(45000, period="month")),
    ("3 500 € brut mensuel", one(3500, period="month")),
    ("Salaire annuel : 45 K€", one(45000, period="year")),
    ("Salaire brut annuel de 45 000 €", one(45000, period="year")),
    ("Rémunération annuelle : 45 000 €", one(45000, period="year")),
    ("Salaire mensuel : 3 500 €", one(3500, period="month")),
    ("45 K€ brut", None),  # no period
    ("45 K brut annuel", None),  # no currency
    ("45 K€ brut annuel variable", one(45000, period="year")),
    ("variable 45 K€ brut annuel", None),  # a bonus word before
    ("45 K€ de prime annuelle", None),
    ("prime annuelle 5 K€ par an", None),
    ("45 K€ selon profil", None),
    ("selon profil", None),
    ("45 K€ brut annuel, 5 K€ de prime", one(45000, period="year")),
    ("12 000 000 € brut annuel", None),  # over 10 million
    ("10 000 000 € brut annuel", one(10000000, period="year")),
    ("45 M€ brut annuel", None),
    ("45 K€ net annuel", one(45000, period="year")),
    ("45 K€ annuel brut", one(45000, period="year")),
    ("au moins 45 K€ brut annuel", None),
    ("plus de 45 K€ brut annuel", None),
    ("dès 45 K€ brut annuel", None),
]

# "$120,000 a year": a sign before, the period after, in English.
DOLLAR_YEAR = [
    ("$120,000 a year", one(120000, "USD", "year")),
    ("$120,000 per year", one(120000, "USD", "year")),
    ("$120,000/yr", one(120000, "USD", "year")),
    ("$120,000 / year", one(120000, "USD", "year")),
    ("$120,000 annually", one(120000, "USD", "year")),
    ("$120,000 yearly", one(120000, "USD", "year")),
    ("$120,000 per annum", one(120000, "USD", "year")),
    ("$120k a year", one(120000, "USD", "year")),
    ("$120K a year", one(120000, "USD", "year")),
    ("$ 120,000 a year", one(120000, "USD", "year")),
    ("US$120,000 a year", one(120000, "USD", "year")),
    ("CA$120,000 a year", one(120000, "CAD", "year")),
    ("A$120,000 a year", one(120000, "AUD", "year")),
    ("AU$120,000 a year", one(120000, "AUD", "year")),
    ("S$120,000 a year", one(120000, "SGD", "year")),
    ("NZ$120,000 a year", one(120000, "NZD", "year")),
    ("HK$120,000 a year", one(120000, "HKD", "year")),
    ("USD 120,000 a year", one(120000, "USD", "year")),
    ("120,000 USD a year", one(120000, "USD", "year")),
    ("$120,000 base a year", one(120000, "USD", "year")),
    ("$60 an hour", one(60, "USD", "hour")),
    ("$60/hour", one(60, "USD", "hour")),
    ("$60/hr", one(60, "USD", "hour")),
    ("$60 hourly", one(60, "USD", "hour")),
    ("$5,000 a month", one(5000, "USD", "month")),
    ("Annual salary: $120,000", one(120000, "USD", "year")),
    ("Hourly rate: $60", one(60, "USD", "hour")),
    ("$120,000", None),  # no period
    ("120,000 a year", None),  # no currency
    ("$120,000 a year in equity", one(120000, "USD", "year")),
    ("Equity: $120,000 a year", None),
    ("$10,000 signing bonus a year", None),  # a bonus word between figure and period
    ("signing bonus of $10,000 a year", None),
    ("up to $120,000 a year", None),
    ("at least $120,000 a year", None),
    ("more than $120,000 a year", None),
    ("over $120,000 a year", None),
    ("$120,000.75 a year", None),  # not whole
    ("$0.99 a year", None),
    ("$12,000,000 a year", None),
    ("$120,000 a years", None),
    ("$1,20,000 a year", None),  # not groups of three
]

# Texts with no figure the list accepts.
NOTHING = [
    "",
    "Competitive salary",
    "Salaire selon profil",
    "Rémunération : selon profil et expérience",
    "0.5% equity",
    "0,1 % à 0,2 % de BSPCE",
    "Bonus: 10%",
    "Call +33 6 12 34 56 78 for the role",
    "+1 (555) 010-0199 per day",
    "Start date 2026-10-20",
    "Published 20/10/2026",
    "Paris 75008, 3 days a week on site",
    "Team of 12 engineers, 4 years of experience",
    "$ per year",
    "€/day",
]


@pytest.mark.parametrize(
    ("text", "expected"),
    RANGE_WORDS + RANGE_K + DAILY + FRENCH_RANGE + FRENCH_YEAR + DOLLAR_YEAR,
)
def test_one_statement(text: str, expected: Pay | None) -> None:
    assert pays(text) == ([expected] if expected else [])


@pytest.mark.parametrize("text", NOTHING)
def test_nothing_stated(text: str) -> None:
    assert pays(text) == []
    assert pay_of(text) is None


def test_pay_of_picks_by_contract_and_keeps_both_statements() -> None:
    text = "Salary 45 K€ brut annuel for a permanent role, or TJM 600 € for a mission."
    assert pays(text) == [one(45000, period="year"), one(600)]
    assert pay_of(text) == one(45000, period="year")
    assert pay_of(text, freelance=True) == one(600)
    # The period not named on one of them: that one is not kept, the other is.
    assert pays("45 K€ for a permanent role, or TJM 600 € for a mission.") == [one(600)]
    assert pays("45 K€ for a permanent role, or 600 € for a mission.") == []
    assert pays("45 K€ brut annuel et 600 €/jour") == [one(45000, period="year"), one(600)]


def test_two_currencies_in_one_text_are_both_read_the_first_kept() -> None:
    text = "€60,000 to €70,000 a year (or £50,000 to £60,000 a year in London)."
    assert pays(text) == [y(60000, 70000), y(50000, 60000, "GBP")]
    assert pay_of(text) == y(60000, 70000)


def test_a_huge_text_is_read_in_one_pass() -> None:
    # 200,000 characters of near misses: digit runs, signs, periods and separators. The parser
    # walks it once (bounded patterns, no backtracking across the run); the figure at the end is
    # still found.
    filler = ("1 2 3 4 5 6 7 8 9 0 € a year 60,0 60.000.000.000.000 € " * 4000)[:199_950]
    text = filler + " TJM 600 €"
    assert len(text) == 199_960
    assert pays(text)[-1] == one(600)
    assert pays("9" * 200_000) == []
    assert pays("1,000," * 33_000 + " EUR a year") == []


@pytest.mark.parametrize(
    ("low", "high", "currency", "period", "text"),
    [
        (45000, 55000, "EUR", "year", "45,000 to 55,000 EUR a year"),
        (600, 600, "EUR", "day", "600 EUR a day"),
        (60, 60, "USD", "hour", "60 USD an hour"),
        (4000, 5000, "GBP", "month", "4,000 to 5,000 GBP a month"),
    ],
)
def test_pay_text(low: int, high: int, currency: str, period: str, text: str) -> None:
    assert pay_text(low, high, currency, period) == text
    for dash in (" - ", "\u2013", "\u2014"):
        assert dash not in text


WORK_MODES = [
    ("Remote", "remote"),
    ("remote", "remote"),
    ("REMOTE", "remote"),
    ("Paris, France, Remote", "remote"),
    ("Remote (Europe)", "remote"),
    ("Remote-friendly", "remote"),
    ("Télétravail", "remote"),
    ("Paris, télétravail", "remote"),
    ("Teletravail", "remote"),
    ("Hybrid", "hybrid"),
    ("Paris (Hybrid)", "hybrid"),
    ("Hybride", "hybrid"),
    ("Lyon, hybride", "hybrid"),
    ("Télétravail partiel", "hybrid"),
    ("Partially remote", "hybrid"),
    ("On site", "office"),
    ("On-site", "office"),
    ("Onsite", "office"),
    ("In office", "office"),
    ("Sur site", "office"),
    ("Présentiel", "office"),
    ("", None),
    ("Paris", None),
    ("Remotely Inc, Paris", None),  # inside a longer word
    ("Hybridge Labs, Paris", None),
    ("Remote / Hybrid", None),  # two modes
    ("Remote or on site", None),
    ("No remote", None),  # negated
    ("Not remote", None),
    ("Non-remote", None),
    ("Pas de télétravail", None),
    ("Sans télétravail", None),
    ("Without remote", None),
    ("Paris, Remote, Remote", "remote"),
    ("Siteonsite", None),
]


@pytest.mark.parametrize(("location", "mode"), WORK_MODES)
def test_work_mode_of(location: str, mode: str | None) -> None:
    assert work_mode_of(location) == mode


@pytest.mark.parametrize(
    ("low", "high", "currency", "period", "expected"),
    [
        (60000, 70000, "EUR", "year", y(60000, 70000)),
        (60000.0, 70000.0, "eur", "year", y(60000, 70000)),
        (600, 600, "GBP", "day", one(600, "GBP")),
        (60000, 70000, "EUR", None, None),
        (60000, 70000, "EUR", "week", None),
        (60000, 70000, "XYZ", "year", None),
        (60000, 70000, None, "year", None),
        (70000, 60000, "EUR", "year", None),
        (0, 70000, "EUR", "year", None),
        (60000, 20_000_000, "EUR", "year", None),
        (45.5, 50, "USD", "hour", None),
        ("60000", 70000, "EUR", "year", None),
        (True, 70000, "EUR", "year", None),
        (None, 70000, "EUR", "year", None),
    ],
)
def test_stated_pay(
    low: Any, high: Any, currency: Any, period: str | None, expected: Pay | None
) -> None:
    assert stated_pay(low, high, currency, period) == expected


# --- what the ATS states ------------------------------------------------------------------------


def _ashby_job(**extra: Any) -> dict[str, Any]:
    return {
        "title": "AI Engineer",
        "location": "Paris",
        "jobUrl": "https://jobs.example.org/a/1",
        "publishedAt": "2026-10-01T08:00:00Z",
        "descriptionPlain": "TJM 600 €",
        **extra,
    }


def _salary(low: Any, high: Any, currency: str = "EUR", interval: str = "1 YEAR") -> dict:
    return {
        "compensationType": "Salary",
        "interval": interval,
        "currencyCode": currency,
        "minValue": low,
        "maxValue": high,
    }


@pytest.mark.parametrize(
    ("extra", "pay", "mode"),
    [
        ({}, None, None),
        ({"compensation": None, "workplaceType": None}, None, None),
        (
            {"compensation": {"summaryComponents": [_salary(60000, 70000)]}},
            y(60000, 70000),
            None,
        ),
        (
            {
                "compensation": {
                    "summaryComponents": [
                        {"compensationType": "EquityPercentage", "minValue": 0.1, "maxValue": 1},
                        _salary(60000, 70000),
                    ]
                },
                "workplaceType": "Hybrid",
            },
            y(60000, 70000),
            "hybrid",
        ),
        (
            {"compensation": {"summaryComponents": [_salary(80, 95, "USD", "1 HOUR")]}},
            y(80, 95, "USD", "hour"),
            None,
        ),
        (
            {
                "compensation": {
                    "compensationTiers": [
                        {"components": [_salary(60000, 70000)]},
                        {"components": [_salary(60000, 70000)]},
                    ]
                }
            },
            y(60000, 70000),
            None,
        ),
        (
            {
                "compensation": {
                    "compensationTiers": [
                        {"components": [_salary(60000, 70000)]},
                        {"components": [_salary(50000, 60000, "GBP")]},
                    ]
                }
            },
            None,  # tiers that disagree: no pay rather than a guess
            None,
        ),
        (
            {"compensation": {"summaryComponents": [_salary(60000, 70000, "EUR", "1 WEEK")]}},
            None,
            None,
        ),
        ({"compensation": {"summaryComponents": [_salary(None, 70000)]}}, None, None),
        ({"compensation": "€60K"}, None, None),
        ({"workplaceType": "Remote"}, None, "remote"),
        ({"workplaceType": "OnSite"}, None, "office"),
        ({"workplaceType": "Mars"}, None, None),
    ],
)
def test_ashby_states_pay_and_work_mode(extra: dict, pay: Pay | None, mode: str | None) -> None:
    posting = ats.parse("ashby", {"jobs": [_ashby_job(**extra)]}, TZ).postings[0]
    assert (posting.pay, posting.work_mode) == (pay, mode)


def test_ashby_board_is_asked_for_its_compensation() -> None:
    assert ats.api_path("ashby", "acme").endswith("?includeCompensation=true")


@pytest.mark.parametrize(
    ("extra", "pay", "mode"),
    [
        ({}, None, None),
        (
            {"salaryRange": {"min": 50000, "max": 60000, "currency": "GBP",
                             "interval": "per-year-salary"}, "workplaceType": "hybrid"},
            y(50000, 60000, "GBP"),
            "hybrid",
        ),
        (
            {"salaryRange": {"min": 90000, "max": 110000, "currency": "CAD",
                             "interval": "per-year-salary"}, "workplaceType": "remote"},
            y(90000, 110000, "CAD"),
            "remote",
        ),
        (
            {"salaryRange": {"min": 40, "max": 55, "currency": "USD",
                             "interval": "per-hour-wage"}, "workplaceType": "onsite"},
            y(40, 55, "USD", "hour"),
            "office",
        ),
        (
            {"salaryRange": {"min": 500, "max": 700, "currency": "EUR",
                             "interval": "one-time"}, "workplaceType": "unspecified"},
            None,
            None,
        ),
        ({"salaryRange": {"min": 60000, "max": 50000, "currency": "EUR",
                          "interval": "per-year-salary"}}, None, None),
        ({"salaryRange": {"min": 60000, "max": 70000, "currency": "ZZZ",
                          "interval": "per-year-salary"}}, None, None),
        ({"salaryRange": []}, None, None),
    ],
)  # fmt: skip
def test_lever_states_pay_and_work_mode(extra: dict, pay: Pay | None, mode: str | None) -> None:
    job = {
        "text": "AI Engineer",
        "categories": {"location": "Paris"},
        "hostedUrl": "https://jobs.example.org/l/1",
        "createdAt": 1_790_000_000_000,
        "descriptionPlain": "Agents.",
        **extra,
    }
    posting = ats.parse("lever", [job], TZ).postings[0]
    assert (posting.pay, posting.work_mode) == (pay, mode)


def test_greenhouse_states_neither() -> None:
    job = {
        "title": "AI Engineer",
        "location": {"name": "Remote"},
        "absolute_url": "https://jobs.example.org/g/1",
        "content": "&lt;p&gt;$120,000 a year&lt;/p&gt;",
    }
    posting = ats.parse("greenhouse", {"jobs": [job]}, TZ).postings[0]
    assert (posting.pay, posting.work_mode) == (None, None)


# --- the sweep, the database, the API -----------------------------------------------------------


def _pay_columns(job: Job) -> tuple[Any, ...]:
    return (job.pay_min, job.pay_max, job.pay_currency, job.pay_period, job.work_mode)


def _postings(factory: sessionmaker[Session], owner: Person) -> dict[str, Job]:
    with factory() as session:
        rows = session.execute(sa.select(Job).where(Job.user_id == owner.user_id)).scalars()
        return {row.title: row for row in rows}


def _sweep(factory: sessionmaker[Session], owner: Person, fake: FakeAts, ats_name: str) -> None:
    set_profile(factory, owner.user_id, deck_search(boards=[board("fake-pay", ats_name)]))
    run(factory, new_sweep(factory, owner.user_id), fake)


@pytest.mark.parametrize(
    ("ats_name", "stated", "unstated_mode"),
    [
        ("ashby", (60000, 70000, "EUR", "year", "hybrid"), None),
        # The fake Lever states "onsite" for a posting that says nothing else.
        ("lever", (50000, 60000, "GBP", "year", "hybrid"), "office"),
        ("greenhouse", (None, None, None, None, None), None),
    ],
)
def test_the_sweep_stores_what_is_stated(
    factory: sessionmaker[Session],
    person_a: Person,
    fake_ats: FakeAts,
    ats_name: str,
    stated: tuple[Any, ...],
    unstated_mode: str | None,
) -> None:
    _sweep(factory, person_a, fake_ats, ats_name)
    found = _postings(factory, person_a)
    assert _pay_columns(found["AI Engineer, Pay Stated"]) == stated
    # No pay from the ATS: the text's daily rate, the location's work mode.
    assert _pay_columns(found["GenAI Engineer, Freelance"]) == (600, 600, "EUR", "day", "remote")
    assert _pay_columns(found["AI Engineer, Pay Not Stated"]) == (None,) * 4 + (unstated_mode,)


def test_a_row_without_pay_and_the_check(
    factory: sessionmaker[Session], person_a: Person, api: Api
) -> None:
    # A row as the release before stores it: every new column null, the card shows nothing.
    posting = add_posting(factory, person_a)
    card = get_deck(api, person_a).json()["cards"][0]
    assert card["id"] == str(posting)
    assert (card["pay"], card["work_mode"]) == (None, None)
    for values in (
        {"pay_min": 600},  # a figure without its currency and period
        {"pay_min": 700, "pay_max": 600, "pay_currency": "EUR", "pay_period": "day"},
        {"pay_min": 0, "pay_max": 600, "pay_currency": "EUR", "pay_period": "day"},
        {"pay_min": 1, "pay_max": 10_000_001, "pay_currency": "EUR", "pay_period": "day"},
        {"pay_min": 600, "pay_max": 600, "pay_currency": "eur", "pay_period": "day"},
    ):
        with factory() as session, pytest.raises(IntegrityError):
            session.execute(sa.update(Job).where(Job.id == posting).values(**values))


def _patch(api: Api, who: Person, posting: uuid.UUID | str, **data: Any) -> Any:
    return api.request("PATCH", f"/api/jobs/{posting}", token=who.token, json=data, headers=CSRF)


def test_a_manual_value_is_kept_through_a_rescore_and_a_new_sweep(
    factory: sessionmaker[Session], person_a: Person, api: Api, fake_ats: FakeAts
) -> None:
    _sweep(factory, person_a, fake_ats, "ashby")
    posting = _postings(factory, person_a)["AI Engineer, Pay Stated"]
    edited = _patch(
        api,
        person_a,
        posting.id,
        pay={"min": 65000, "max": 75000, "currency": "EUR", "period": "year"},
        work_mode="remote",
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["pay_min"], body["pay_max"], body["pay_currency"], body["pay_period"]) == (
        65000,
        75000,
        "EUR",
        "year",
    )
    assert body["work_mode"] == "remote"
    edit = next(event for event in body["events"] if event["kind"] == "edit")
    assert edit["data"]["changes"]["pay_min"] == {"from": 60000, "to": 65000}

    rescored = api.post("/api/jobs/rescore", token=person_a.token, json={}, headers=CSRF)
    assert rescored.status_code in (200, 202), rescored.text
    run(factory, new_sweep(factory, person_a.user_id), fake_ats)
    after = _postings(factory, person_a)["AI Engineer, Pay Stated"]
    assert _pay_columns(after) == (65000, 75000, "EUR", "year", "remote")

    card = next(c for c in get_deck(api, person_a).json()["cards"] if c["id"] == str(posting.id))
    assert (card["pay"], card["work_mode"]) == ("65,000 to 75,000 EUR a year", "remote")

    # Removed by hand: null, and nothing comes back.
    cleared = _patch(api, person_a, posting.id, pay=None, work_mode=None)
    assert cleared.status_code == 200, cleared.text
    run(factory, new_sweep(factory, person_a.user_id), fake_ats)
    assert _pay_columns(_postings(factory, person_a)["AI Engineer, Pay Stated"]) == (None,) * 5


@pytest.mark.parametrize(
    "pay",
    [
        {"min": 70000, "max": 60000, "currency": "EUR", "period": "year"},
        {"min": 0, "max": 60000, "currency": "EUR", "period": "year"},
        {"min": 1, "max": 10_000_001, "currency": "EUR", "period": "year"},
        {"min": 60000, "max": 70000, "currency": "XYZ", "period": "year"},
        {"min": 60000, "max": 70000, "currency": "EUR", "period": "week"},
        {"min": 60000, "max": 70000, "currency": "EUR"},
        {"min": "60000", "max": 70000, "currency": "EUR", "period": "year"},
        {"min": 60000.5, "max": 70000, "currency": "EUR", "period": "year"},
        {"min": 60000, "max": 70000, "currency": "EUR", "period": "year", "bonus": 1},
        "60k",
    ],
)
def test_a_pay_patch_is_checked(
    factory: sessionmaker[Session], person_a: Person, api: Api, pay: Any
) -> None:
    posting = add_posting(factory, person_a)
    assert _patch(api, person_a, posting, pay=pay).status_code == 422
    assert _patch(api, person_a, posting, work_mode="moon").status_code == 422


def test_the_detail_and_the_list_show_them_and_b_never_reads_a(
    factory: sessionmaker[Session], person_a: Person, person_b: Person, api: Api
) -> None:
    posting = add_posting(
        factory,
        person_a,
        pay_min=600,
        pay_max=600,
        pay_currency="EUR",
        pay_period="day",
        work_mode="hybrid",
    )
    detail = api.get(f"/api/jobs/{posting}", token=person_a.token).json()
    assert (detail["pay_min"], detail["pay_period"], detail["work_mode"]) == (600, "day", "hybrid")
    listed = api.get("/api/jobs", token=person_a.token).json()["items"][0]
    assert (listed["pay_currency"], listed["work_mode"]) == ("EUR", "hybrid")
    card = get_deck(api, person_a).json()["cards"][0]
    assert (card["pay"], card["work_mode"]) == ("600 EUR a day", "hybrid")

    assert api.get(f"/api/jobs/{posting}", token=person_b.token).status_code == 404
    assert _patch(api, person_b, posting, work_mode="remote").status_code == 404
    assert get_deck(api, person_b).json()["cards"] == []
    with factory() as session:
        assert session.get(Job, posting).work_mode == "hybrid"


def test_the_deck_filter_reads_the_stated_work_mode(
    factory: sessionmaker[Session], person_a: Person, api: Api
) -> None:
    stated = add_posting(factory, person_a, location="Paris", work_mode="remote")
    named = add_posting(factory, person_a, location="Remote, Europe")
    office = add_posting(factory, person_a, location="Remote office, Paris", work_mode="office")
    plain = add_posting(factory, person_a, location="Paris")

    def ids(remote: str) -> set[str]:
        return {c["id"] for c in get_deck(api, person_a, remote=remote).json()["cards"]}

    assert ids("remote") == {str(stated), str(named)}
    assert ids("on_site") == {str(office), str(plain)}


def test_a_cybersecurity_posting_with_a_pay_never_shows(
    factory: sessionmaker[Session], person_a: Person, api: Api
) -> None:
    add_posting(
        factory,
        person_a,
        title="Cybersecurity Engineer",
        pay_min=600,
        pay_max=600,
        pay_currency="EUR",
        pay_period="day",
    )
    assert get_deck(api, person_a).json()["cards"] == []
