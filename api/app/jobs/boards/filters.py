"""Which postings of a company board a sweep keeps: the person's saved criteria, applied by code
after reading, before the store (a board lists every opening of the company, so unlike a job
website's search nothing narrowed it before).

In order, the first that fails names the reason (counted per board in the sweep's stats):
- title: no wanted title (title_include) matches the title;
- excluded: an unwanted title (title_exclude) matches it;
- location: no wanted place matches the location. A saved place that says no limit
  ("everywhere", "worldwide", "partout": browser.finder.worldwide) removes this check, as an
  empty list does;
- location_excluded: an unwanted place matches the location;
- too_old: the ATS states a date older than the saved `days`; an unknown date is kept;
- contract: the saved contract types do not hold the posting's (`contract_of`, the one the stored
  job shows too); with no saved contract type, no check.
A posting has one contract (`contract_of`): a word of its title, location or address that names
one other than CDI (the importer's `kind`: "Internship", "Stage", "CDD", "Freelance", ...) wins;
else the contract the ATS states when its words map (ats.contract_of: "permanent" is CDI, "Full
time" names a schedule and maps to nothing); else CDI. So an ATS that lists an internship as
FullTime, or a "Stage" as permanent, never makes it a CDI.
The never apply rules are not a filter here: the store keeps a matching posting as `skipped`
with the rule as its note, as for every other source. The remote stance is not checked: the ATSs
state it in too many ways (declared gap).
Terms match as everywhere in the app (profile.matching): plain words as whole words with an
optional plural, case aside; a list saved as regular expressions as the expressions it holds.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from re import Pattern

from app.jobs.boards.ats import BoardPosting
from app.jobs.importer import kind
from app.jobs.models import ContractType
from app.profile import matching
from app.profile.schemas import SearchSection, TermList


def contract_of(posting: BoardPosting) -> ContractType:
    """The posting's one contract, filtered and stored (module docstring)."""
    said = kind(posting.title, posting.location, posting.url)
    if said is not ContractType.CDI:  # `kind` answers CDI only when no other word is there
        return said
    return posting.contract or ContractType.CDI


def _compiled(terms: TermList) -> Pattern[str] | None:
    return matching.compile_terms(list(terms.terms), terms.advanced_regex)


@dataclass(frozen=True)
class BoardFilter:
    include: Pattern[str] | None
    exclude: Pattern[str] | None
    location: Pattern[str] | None
    location_exclude: Pattern[str] | None
    contracts: frozenset[str]
    since: str  # YYYY-MM-DD, the oldest date kept

    @classmethod
    def of(cls, search: SearchSection, today: date) -> "BoardFilter":
        from app.browser.finder import worldwide

        places = search.location
        anywhere = any(worldwide(term) for term in places.terms)
        return cls(
            include=_compiled(search.title_include),
            exclude=_compiled(search.title_exclude),
            location=None if anywhere else _compiled(places),
            location_exclude=_compiled(search.location_exclude),
            contracts=frozenset(search.contract_types),
            since=(today - timedelta(days=search.days)).isoformat(),
        )

    def reason(self, posting: BoardPosting, contract: ContractType | None = None) -> str | None:
        """Why the posting is left out, None when it is kept; `contract` is contract_of's, when
        the caller has it already."""
        if self.include is not None and not self.include.search(posting.title):
            return "title"
        if self.exclude is not None and self.exclude.search(posting.title):
            return "excluded"
        if self.location is not None and not self.location.search(posting.location):
            return "location"
        if self.location_exclude is not None and self.location_exclude.search(posting.location):
            return "location_excluded"
        if posting.published and posting.published < self.since:
            return "too_old"
        if self.contracts:
            contract = contract or contract_of(posting)
            if contract.value not in self.contracts:
                return "contract"
        return None
