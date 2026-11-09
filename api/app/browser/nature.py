"""The contract every posting of a job website has by nature (B21, owner 2026-11-04), in a module
that imports no agent: the website search (app/browser/sources/interactive.py) and the posting
reader (app/jobs/reader.py) both read it, and the reader must not register the finder's agents."""

from urllib.parse import urlsplit

from app.jobs.models import ContractType

# By the start's exact host: the VIE website (`mon-vie-via.businessfrance.fr`, whose postings
# `kind` reads as VIE from their address), and its stand in for the tests on `.test`, a name
# reserved by RFC 6761 that is never a public host. Ticking one is the person's choice of that
# contract, so its postings are never dropped as of another contract.
NATURE_CONTRACTS = {
    "mon-vie-via.businessfrance.fr": ContractType.VIE.value,
    "mon-vie-via.businessfrance.test": ContractType.VIE.value,
}


def nature_contract(start: str) -> str | None:
    """The contract a website's postings have by nature, read from its start address."""
    try:
        return NATURE_CONTRACTS.get((urlsplit(start).hostname or "").rstrip("."))
    except ValueError:
        return None
