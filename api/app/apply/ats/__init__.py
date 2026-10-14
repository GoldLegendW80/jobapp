"""The ATS strategies of the apply module (SPEC-apply, "Per ATS strategies"), in the order they
are tried: by the posting's address first, then by the page (a form a company site embeds).
Each later adapter adds its file and one line to `ADAPTERS`."""

from app.apply.ats.ashby import Ashby
from app.apply.ats.base import Adapter
from app.apply.ats.greenhouse import Greenhouse

ADAPTERS: tuple[type[Adapter], ...] = (
    Ashby,
    Greenhouse,
    # Lever,  # apply A8, lever.py
    # Workday,  # apply A15, workday.py
    # GoogleCareers,  # apply A15, google_careers.py
    # Vie,  # apply A15, vie.py
    # LinkedIn,  # apply A16, linkedin.py
    # Generic,  # apply A8, generic.py: last, it takes any page
)


def adapter_for(
    url: str, snapshot: str | None = None, *, stub_base: str | None = None
) -> Adapter | None:
    """The adapter of a posting address, else of the page it shows; None when no adapter knows
    it (the generic strategy of A8, or the person)."""
    adapters = [cls(stub_base) for cls in ADAPTERS]
    for adapter in adapters:
        if adapter.form_url(url) is not None:
            return adapter
    if snapshot is not None:
        for adapter in adapters:
            if adapter.recognises(snapshot):
                return adapter
    return None
