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
from app.jobs.models import ContractType, Job, SweepStatus
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
    ("2025-2026 € per year", None),  # years next to a sign, not a pay
    ("2 025-2 026 € per year", y(2025, 2026)),  # grouped: a figure, read as written
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
    ("45 K€ brut annuel variable", None),  # a bonus word right after
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
    ("$120,000 a year in equity", None),  # a bonus word right after
    ("$120,000 a year plus equity", one(120000, "USD", "year")),
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

# A bonus or a perk is never a pay (review of PR #498): the words before the figure, right after
# it, a comma or a parenthesis after the word, the floor of each period, a year next to a sign.
PERKS = [
    ("Tickets restaurant 9 € par jour", None),
    ("Ticket restaurant de 9 € par jour", None),
    ("Titres-restaurant : 10 € par jour", None),
    ("Carte Swile 9 € par jour", None),
    ("Meal vouchers worth €8 a day", None),
    ("Lunch: €10 a day", None),
    ("Mutuelle prise en charge 50 € par mois", None),
    ("Gym membership $50 a month", None),
    ("Learning budget of €1,000 a year", None),
    ("Training budget: €2,000/year", None),
    ("Remote budget €500 per year", None),
    ("Home office stipend $1,000 per year", None),
    ("€3,000 per year for conferences", None),
    ("€10k per month in cloud credits", None),
    ("Budget formation 2 000 € par an", None),
    ("Pass Navigo remboursé 86 € par mois", None),
    ("€1,500 a year for training", None),
    ("Bonus, €5,000 per year", None),
    ("Bonus , €5,000 per year", None),
    ("Bonus (€5,000 per year)", None),
    ("Prime, 3 000 € par an", None),
    ("Signing bonus, paid once: $10,000 a year", None),
    ("€9 a day", None),  # under the day floor: a perk, never a pay
    ("€90 a month", None),  # under the month floor
    ("€900 a year", None),  # under the year floor
    ("2026 € a year", None),  # a year next to a sign
    ("2026€ par mois", one(2026, period="month")),  # O3b: a month is never a year
    ("€20 a day", one(20)),  # the floors themselves are kept
    ("€100 a month", one(100, period="month")),
    ("€1,000 a year", one(1000, period="year")),
    ("€2026 a year", one(2026, period="year")),  # a sign before: a figure
    ("2026 € brut par mois", one(2026, period="month")),  # "brut": a pay
    ("TJM 2000 €", one(2000)),  # "TJM": a pay
    ("2 026 € par mois", one(2026, period="month")),  # grouped: a figure
    ("$120,000 a year plus bonus", one(120000, "USD", "year")),
    ("45 K€ brut annuel, 5 K€ de prime", one(45000, period="year")),
    ("Machine Learning Engineer, $150k-$200k a year", y(150000, 200000, "USD")),
    ("Salary €60k-€70k a year and a learning budget", y(60000, 70000)),
    ("Up to €60,000 to €70,000 per year", y(60000, 70000)),  # a range: no open bound
]

# O3b (issue #504, the re-check of PR #498), follow-up 1: profit shares, a thirteenth month,
# sign-on payments, options and on target earnings are bonuses, never a base pay.
BONUS_WORDS = [
    ("Intéressement 3 000 € par an", None),
    ("Intéressement : 3 000 € par an", None),
    ("interessement 3 000 € par an", None),
    ("Participation aux bénéfices 2 500 € par an", None),
    ("Participation, 2 500 € par an", None),
    ("Abondement PEE 1 500 € par an", None),
    ("Abondement de 1 500 € par an", None),
    ("13e mois, 3 000 € par an", None),
    ("13ème mois : 3 000 € par an", None),
    ("13 ème mois 3 000 € par an", None),
    ("Treizième mois de 3 000 € par an", None),
    ("13th month salary $5,000 a year", None),
    ("Profit sharing $5,000 a year", None),
    ("Profit-sharing: $5,000 a year", None),
    ("Gain sharing $2,000 a year", None),
    ("Sign-on $10,000 a year", None),
    ("Sign on bonus $10,000 a year", None),
    ("Signon $10,000 a year", None),
    ("Options worth $50,000 a year", None),
    ("Stock options: $50,000 a year", None),
    ("OTE $150,000 a year", None),
    ("OTE: €90,000 per year", None),
    ("On target earnings of $150,000 a year", None),
    ("On-target earnings $150,000 a year", None),
    ("€60,000 a year in options", None),
    ("45 000 € brut par an d'intéressement", None),
    ("Salaire 45 000 € brut par an, intéressement en sus", one(45000, period="year")),
    ("€60,000 a year. Options available.", one(60000, period="year")),
    ("Notes: €60,000 a year", one(60000, period="year")),  # "ote" inside a word
    ("Base salary $150,000 a year, OTE $200,000 a year", one(150000, "USD", "year")),
    ("Participative culture, €60,000 a year", one(60000, period="year")),
    ("13 000 € par an", one(13000, period="year")),
    ("Optional remote, €60,000 a year", one(60000, period="year")),
    ("Signal processing engineer, €60,000 a year", one(60000, period="year")),
]

# Follow-up 2: the year guard reads a yearly statement only, and a pay word earlier in the
# clause is a sign of a pay ("2000 € par mois" is a French intern's or junior's pay).
YEAR_GUARD = [
    ("2000 € par mois", one(2000, period="month")),
    ("2000€/mois", one(2000, period="month")),
    ("2000 €/mois", one(2000, period="month")),
    ("2000 € a month", one(2000, period="month")),
    ("Salaire 2000 € par mois", one(2000, period="month")),
    ("Gratification 2000 € par mois", one(2000, period="month")),
    ("1900 € par mois", one(1900, period="month")),
    ("2100 € par mois", one(2100, period="month")),
    ("1950€ mensuel", one(1950, period="month")),
    ("2 000 € par mois", one(2000, period="month")),
    ("2000 € par jour", one(2000)),
    ("2000 €/h", one(2000, period="hour")),
    ("Salaire 2026 € par an", one(2026, period="year")),
    ("Salary 2000 € a year", one(2000, period="year")),
    ("Rémunération 2000 € par an", one(2000, period="year")),
    ("Remuneration: 2000 € per year", one(2000, period="year")),
    ("Gratification de stage 2000 € par an", one(2000, period="year")),
    ("Wage 2026 € a year", one(2026, period="year")),
    ("Salary 2026 $ a year", one(2026, "USD", "year")),
    ("2026 € a year", None),
    ("2000 € par an", None),
    ("2026€/an", None),
    ("En 2026 € par an", None),
    ("Depuis 2019 € par an", None),
    ("Paid 2026 € a year", None),  # "paid" names no pay
    ("Salaire. 2026 € par an", None),  # the pay word ends with its clause
    # Review of #513, follow-up 5: a date word right before the figure makes it a year, whatever
    # pay word comes earlier.
    ("Salary review in 2026 € a year", None),
    ("Révision salariale en 2026 € par an", None),
    ("Salary since 2019 € a year", None),
    ("Salary of 2026 € a year", one(2026, period="year")),
    ("2025-2026 € per year", None),
    ("2000 € par mois de prime", None),
    ("Prime 2000 € par mois", None),
    ("Salaire 2000 € par mois. Prime 2000 € par mois", one(2000, period="month")),
]

# Follow-up 3: a mark right after a bonus word ends no clause, nor does a line break the figure
# follows at once; a line break followed by words does.
LABEL_MARKS = [
    ("Bonus; €5,000 per year", None),
    ("Bonus ;€5,000 per year", None),
    ("Bonus | €5,000 per year", None),
    ("Bonus • €5,000 per year", None),
    ("Bonus + €5,000 per year", None),
    ("Bonus\n€5,000 per year", None),
    ("Bonus\r\n€5,000 per year", None),
    ("Bonus\n\n€5,000 per year", None),
    ("Bonus:\n€5,000 per year", None),
    ("Bonus;\n€5,000 per year", None),
    ("Bonus.\n€5,000 per year", None),
    ("Bonus,  , €5,000 per year", None),
    ("Bonus (annual) €5,000 per year", None),
    ("Prime ; 3 000 € par an", None),
    ("Prime | 3 000 € par an", None),
    ("Prime\n3 000 € par an", None),
    ("Equity • $50,000 a year", None),
    ("Mutuelle ; 50 € par mois", None),
    ("Benefits\nSalary: €60,000 a year", one(60000, period="year")),
    ("Bonus\nSalary: €60,000 a year", one(60000, period="year")),
    ("Bonus\n\nSalary\n€60,000 a year", one(60000, period="year")),
    ("Great bonus. €60,000 a year", one(60000, period="year")),
    ("Salary; €60,000 per year", one(60000, period="year")),
    ("Salaire | 45 000 € par an", one(45000, period="year")),
    ("Remote • €60,000 a year", one(60000, period="year")),
    ("Mutuelle.\nSalaire : 2 500 € par mois", one(2500, period="month")),
    ("Prime\nSalaire 2 500 € par mois", one(2500, period="month")),
    ("Tickets restaurant\nTJM 600 €", one(600)),
    ("Salary\n€60,000 a year", one(60000, period="year")),
    ("Salaire :\n45 000 € par an", one(45000, period="year")),
]

# Follow-up 4: after the statement, a bonus or perk word anywhere in its clause refuses it,
# except after an opening "with", "avec", "hors", "excluding", "without" or "plus" (an addition
# to a full pay), or in a role after "for a" or "as a".
AFTER_CLAUSE = [
    ("Salary: €70,000 per year with great benefits", one(70000, period="year")),
    ("$150,000 a year with equity", one(150000, "USD", "year")),
    ("40 000 € brut par an hors variable", one(40000, period="year")),
    ("40 000 € brut par an avec mutuelle", one(40000, period="year")),
    ("Salary: €50,000 per year for a Training Manager", one(50000, period="year")),
    ("$150,000 a year excluding bonus", one(150000, "USD", "year")),
    ("€60,000 a year with a learning budget", one(60000, period="year")),
    ("45 000 € par an avec tickets restaurant", one(45000, period="year")),
    ("45 000 € par an hors prime", one(45000, period="year")),
    ("€60,000 a year as a Senior Equity Analyst", one(60000, period="year")),
    ("600 € par jour avec mutuelle", one(600)),
    ("$140,000 per year with stock options", one(140000, "USD", "year")),
    ("€60,000 per year, with bonus", one(60000, period="year")),
    ("€60,000 a year + bonus", one(60000, period="year")),
    ("€60,000 a year without commission", one(60000, period="year")),
    ("$120,000 a year in equity", None),
    ("$120,000 a year in stock", None),
    ("€60,000 a year in RSUs", None),
    ("€60,000 a year of equity", None),
    ("€60,000 a year including equity", None),
    ("€60,000 a year y compris variable", None),
    ("€3,000 per year for conferences", None),
    ("€1,500 a year for training", None),
    ("€1,000 a year for a training budget", None),
    ("€1,000 a year for your home office", None),
    ("€500 a year for the gym", None),
    ("€10k per month in cloud credits", None),
    ("€2,000 per year towards childcare", None),
    ("45 000 € par an de prime", None),
    ("3 000 € par an d'intéressement", None),
    ("1 500 € par an en tickets restaurant", None),
    ("500 € par an pour la formation", None),
    ("45 K€ brut annuel variable", None),
    ("€5,000 a year bonus", None),
    ("$10,000 a year signing bonus", None),
    ("€2,000 per year as a bonus", None),
    # Review of #513, blocker 1: a refused word anywhere in the clause after the figure refuses
    # it (#498's rule); only an opening addition word or a role keeps the figure.
    ("$5,000 a year annual bonus", None),
    ("€5,000 per year performance bonus", None),
    ("€5,000 per year target bonus", None),
    ("€5,000 a year paid as bonus", None),
    ("€1,000 per year learning budget", None),
    ("€2,000 per year personal development budget", None),
    ("€1,000 a year education stipend", None),
    ("€300 per month remote work allowance", None),
    ("€1,000 a year to spend on conferences", None),
    ("€2,000 per year to spend on training", None),
    ("€500 per month to cover your gym membership", None),
    ("€100 per month per employee for lunch", None),
    ("€1,500 par an à utiliser en formation", None),
    ("€5,000 a year: bonus", None),
    ("€5,000 a year - bonus", None),
    ("1 000 € par an de budget formation", None),
    ("200 € par mois d'indemnité télétravail", None),
    ("1 500 € par an pour vos frais de formation", None),
    ("3 000 € par an versés en prime", None),
    ("€60,000 a year paid monthly", one(60000, period="year")),
    ("€60,000 a year based in Paris", one(60000, period="year")),
    ("60 000 € par an en télétravail", one(60000, period="year")),  # no perk word: a pay
    ("€60,000 a year plus bonus", one(60000, period="year")),
    ("45 000 € par an plus prime", one(45000, period="year")),
    # Follow-up 2: "on target earnings" is refused, "on" is no linking word there.
    ("€60,000 a year on target earnings", None),
    ("€60,000 a year OTE", None),
    ("€60,000 a year on site", one(60000, period="year")),
    # Blocker 2: a capitalised run after "for a" or "as a" is a role only when its last word
    # is not a bonus or perk word.
    ("€60,000 a year for a Senior Equity Analyst", one(60000, period="year")),
    ("€45,000 per year as a Training Manager", one(45000, period="year")),
    ("€60,000 a year for a Sales Manager with commission", one(60000, period="year")),
    ("€5,000 per year as a Performance Bonus", None),
    ("€1,000 per year for a Learning Budget", None),
    ("€3,000 per year for a Gym Membership", None),
    ("€2,000 a year as a Signing Bonus", None),
    ("€1,000 a year for a Training budget", None),  # the run ends on "Training"
    # Follow-up 3: an addition that names a bonus or a perk keeps a full pay only (SALARY_MIN:
    # 10,000 a year, 500 a month, 50 a day, 5 an hour); under it the figure is the addition's.
    ("€5,000 a year with stock options", None),
    ("€9,999 a year with stock options", None),
    ("€10,000 a year with stock options", one(10000, period="year")),
    ("€300 per month with meal vouchers", None),
    ("€2,500 per month with meal vouchers", one(2500, period="month")),
    ("40 € par jour avec tickets restaurant", None),
    ("€5,000 a year with a nice team", one(5000, period="year")),  # no refused word
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
    RANGE_WORDS
    + RANGE_K
    + DAILY
    + FRENCH_RANGE
    + FRENCH_YEAR
    + DOLLAR_YEAR
    + PERKS
    + BONUS_WORDS
    + YEAR_GUARD
    + LABEL_MARKS
    + AFTER_CLAUSE,
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


def test_pay_of_never_falls_back_to_a_refused_figure() -> None:
    # A permanent role whose only figures are perks: no pay, whatever the contract.
    text = (
        "CDI. Tickets restaurant 9 € par jour, mutuelle 50 € par mois, "
        "budget formation 2 000 € par an."
    )
    assert pays(text) == []
    assert pay_of(text) is None
    assert pay_of(text, freelance=True) is None
    # The salary is kept, the perk beside it is not, in either order.
    salary = "Salaire 45 K€ brut annuel. Tickets restaurant 9 € par jour."
    assert pay_of(salary) == one(45000, period="year")
    assert pay_of("Tickets restaurant 9 € par jour. TJM 600 €", freelance=False) == one(600)


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
        # json reads NaN, Infinity and 1e400: no pay, never an exception (review of PR #498).
        (float("nan"), 70000, "EUR", "year", None),
        (60000, float("nan"), "EUR", "year", None),
        (float("inf"), 70000, "EUR", "year", None),
        (60000, float("inf"), "EUR", "year", None),
        (float("-inf"), 70000, "EUR", "year", None),
        (60000, float("-inf"), "EUR", "year", None),
        (1e400, 1e400, "EUR", "year", None),
        (10**400, 10**400, "EUR", "year", None),
        (1e300, 1e300, "EUR", "year", None),
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
        ({"compensation": {"summaryComponents": [_salary(60000, 70000, 5)]}}, None, None),
        ({"compensation": {"compensationTiers": [None, 7, {"components": "x"}]}}, None, None),
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
        ({"workplaceType": "on-site"}, None, "office"),  # Lever's docs spell it so
        ({"salaryRange": {"min": 50000, "max": 60000, "currency": 7,
                          "interval": "per-year-salary"}}, None, None),
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


BAD_FIGURES = ("NaN", "Infinity", "-Infinity", "1e400")


@pytest.mark.parametrize("figure", BAD_FIGURES)
@pytest.mark.parametrize("side", ["min", "max"])
def test_ashby_bad_figures_are_no_pay(figure: str, side: str) -> None:
    low, high = (figure, "70000") if side == "min" else ("60000", figure)
    raw = (
        '{"jobs":[{"title":"AI Engineer","jobUrl":"https://jobs.example.org/a/1",'
        '"workplaceType":"Remote","compensation":{"summaryComponents":[{"compensationType":'
        f'"Salary","interval":"1 YEAR","currencyCode":"EUR","minValue":{low},"maxValue":{high}}}'
        ']}},{"title":"AI Engineer 2","jobUrl":"https://jobs.example.org/a/2"}]}'
    ).encode()
    result = ats.decode("ashby", raw, TZ)
    assert [(p.title, p.pay, p.work_mode) for p in result.postings] == [
        ("AI Engineer", None, "remote"),
        ("AI Engineer 2", None, None),
    ]
    assert result.invalid == 0


@pytest.mark.parametrize("figure", BAD_FIGURES)
@pytest.mark.parametrize("side", ["min", "max"])
def test_lever_bad_figures_are_no_pay(figure: str, side: str) -> None:
    low, high = (figure, "60000") if side == "min" else ("50000", figure)
    raw = (
        '[{"text":"AI Engineer","hostedUrl":"https://jobs.example.org/l/1",'
        '"workplaceType":"hybrid","salaryRange":{"min":' + low + ',"max":' + high + ","
        '"currency":"EUR","interval":"per-year-salary"}}]'
    ).encode()
    result = ats.decode("lever", raw, TZ)
    assert [(p.title, p.pay, p.work_mode) for p in result.postings] == [
        ("AI Engineer", None, "hybrid")
    ]


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


@pytest.mark.parametrize(("ats_name", "broken_mode"), [("ashby", "hybrid"), ("lever", "office")])
def test_a_board_with_bad_pay_never_stalls_the_sweep(
    factory: sessionmaker[Session],
    person_a: Person,
    fake_ats: FakeAts,
    ats_name: str,
    broken_mode: str,
) -> None:
    # NaN and Infinity in the ATS's pay (review of PR #498): that posting is stored without a
    # pay, the board is done, the good board beside it too, and the sweep ends done, not
    # timed out. The meal voucher is no pay.
    boards = [board("fake-pay-bad", ats_name), board("fake-ok", ats_name)]
    set_profile(factory, person_a.user_id, deck_search(boards=boards))
    row = run(factory, new_sweep(factory, person_a.user_id), fake_ats, timeout_s=30)
    assert row.status is SweepStatus.done, row.error
    s = row.stats
    assert (s["boards_total"], s["boards_done"], s["boards_ok"], s["boards_failed"]) == (
        2, 2, 2, [],
    )  # fmt: skip
    found = _postings(factory, person_a)
    assert {"AI Engineer", "Applied AI Engineer, Agents"} <= set(found)  # the good board
    assert _pay_columns(found["AI Engineer, Pay Broken"]) == (None,) * 4 + (broken_mode,)
    meal = found["AI Engineer, Meal Vouchers"]
    assert meal.contract_type is ContractType.CDI
    assert _pay_columns(meal)[:4] == (None,) * 4


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
    hybrid = add_posting(factory, person_a, location="Remote, Paris", work_mode="hybrid")

    def ids(remote: str) -> set[str]:
        return {c["id"] for c in get_deck(api, person_a, remote=remote).json()["cards"]}

    assert ids("remote") == {str(stated), str(named)}
    assert ids("on_site") == {str(office), str(plain)}
    # Hybrid is neither remote nor on site; with no filter it shows.
    assert str(hybrid) in {c["id"] for c in get_deck(api, person_a).json()["cards"]}


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
