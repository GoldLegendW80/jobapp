"""The ATS strategies of the apply module (SPEC-apply, "Per ATS strategies"), in the order they
are tried: by the posting's address first, then by the page (a form a company site embeds).
Each later adapter adds its file and one line to `ADAPTERS`."""

from app.apply.ats.ashby import Ashby
from app.apply.ats.base import Adapter
from app.apply.ats.generic import Generic
from app.apply.ats.greenhouse import Greenhouse
from app.apply.ats.lever import Lever

ADAPTERS: tuple[type[Adapter], ...] = (
    Ashby,
    Greenhouse,
    Lever,
    # Workday,  # apply A15, workday.py
    # GoogleCareers,  # apply A15, google_careers.py
    # Vie,  # apply A15, vie.py
    # LinkedIn,  # apply A16, linkedin.py
    Generic,  # last: any page that shows a field, filled by the form agent in Review first only
)


def adapter_for(
    url: str, snapshot: str | None = None, *, stub_base: str | None = None
) -> Adapter | None:
    """The adapter of a posting address, else of the page it shows; None when no adapter knows
    it (the generic strategy of A8, or the person). An adapter chosen by the page is `framed`:
    the page must frame the form in one iframe, and the fill reads only that iframe. The
    frame's host cannot be checked from the snapshot (see `base`), so a caller deciding about
    Full auto reads `adapter.framed`."""
    adapters = [cls(stub_base) for cls in ADAPTERS]
    for adapter in adapters:
        if adapter.form_url(url) is not None:
            return adapter
    if snapshot is not None:
        for adapter in adapters:
            if adapter.recognises(snapshot):
                return type(adapter)(stub_base, framed=True)
    return None
