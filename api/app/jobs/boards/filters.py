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
- contract: the saved contract types do not hold the posting's. Its contract is the one the ATS
  states when it maps (ats.contract_of), else the one its title, location and address say (the
  importer's `kind`, which the stored job shows too); with no saved contract type, no check.
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
from app.profile import matching
from app.profile.schemas import SearchSection, TermList


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

    def reason(self, posting: BoardPosting) -> str | None:
        """Why the posting is left out, None when it is kept."""
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
            contract = posting.contract or kind(posting.title, posting.location, posting.url)
            if contract.value not in self.contracts:
                return "contract"
        return None
