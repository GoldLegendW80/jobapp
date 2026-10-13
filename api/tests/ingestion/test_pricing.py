"""pricing.py: every model a run can choose has a price, the three costs, the rounding rule."""

import uuid
from decimal import Decimal
from fractions import Fraction

import pytest

import app.main  # noqa: F401  (registers every agent of every module, as the api does)
from app.agents import definitions
from app.agents.models import CredentialKind
from app.ingestion import estimate, plan, pricing

SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"


def models_a_run_can_choose() -> set[str]:
    """The models of this module's runs, and of every agent the runner knows: its own model and
    each model it names for a kind of credential."""
    chosen = set(plan.models())
    for agent in definitions.AGENTS.values():
        chosen.add(agent.model)
        chosen.update((agent.model_by_credential or {}).values())
    return chosen


def without_a_price() -> set[str]:
    return {model for model in models_a_run_can_choose() if model not in pricing.PRICES}


def dossier(*characters: int) -> estimate.RunEstimate:
    documents = [
        plan.Document(uuid.UUID(int=n + 1), f"{n:064x}", size) for n, size in enumerate(characters)
    ]
    return estimate.run(plan.build(documents, model=OPUS).steps)


def test_every_model_a_run_can_choose_has_a_price() -> None:
    assert {OPUS, SONNET} <= models_a_run_can_choose()
    assert without_a_price() == set()


def test_a_model_added_without_a_price_fails_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(plan.MODEL_BY_CREDENTIAL, "claude_subscription", "claude-next")
    assert without_a_price() == {"claude-next"}
    with pytest.raises(LookupError, match="claude-next"):
        pricing.costs(dossier(10), "claude-next")


def test_an_agent_registered_with_an_unpriced_model_fails_the_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dataclasses

    other = dataclasses.replace(definitions.AGENTS["echo"], name="unpriced", model="claude-other")
    monkeypatch.setitem(definitions.AGENTS, "unpriced", other)
    assert without_a_price() == {"claude-other"}


# The pricing page as read on the day of `PRICES_AS_OF`, in US dollars per million tokens:
# (base input, 5 minute cache write, cache hit, output). Written out figure by figure, so the
# table of `pricing.py` can only change together with this one and with the date.
PAGE_READ_ON = "2026-10-13"
PAGE = {
    OPUS: ("4", "5", "0.20", "20"),
    SONNET: ("2", "2.50", "0.10", "10"),
}


def test_the_table_is_dated_and_names_its_source() -> None:
    assert pricing.PRICES_AS_OF.isoformat() == PAGE_READ_ON
    assert pricing.PRICES_AS_OF.isoformat() in pricing.ESTIMATE_NOTE
    assert pricing.PRICES_URL == "https://platform.claude.com/docs/en/about-claude/pricing"


@pytest.mark.parametrize("model", sorted(PAGE))
def test_every_price_of_a_model_is_the_figure_of_the_pricing_page(model: str) -> None:
    """Each figure as a literal, the cache read included: an ordering alone let a cache read of
    0.20 pass for the second model, whose page figure is 0.10 (review of PR #112, B1)."""
    base, write, hit, out = (Fraction(figure) for figure in PAGE[model])
    price = pricing.PRICES[model]
    assert (price.input, price.cache_write, price.cache_read, price.output) == (
        base,
        write,
        hit,
        out,
    )
    assert price.cache_read == price.input / 20  # the page: a cache hit is 0.05 times the input
    assert 0 < price.cache_read < price.input < price.cache_write < price.output


def test_the_page_figures_cover_exactly_the_priced_models() -> None:
    assert set(PAGE) == set(pricing.PRICES)


def test_only_an_api_key_is_priced() -> None:
    assert pricing.priced(CredentialKind.anthropic_api_key)
    assert pricing.priced("anthropic_api_key")
    assert not pricing.priced(CredentialKind.claude_subscription)
    assert not pricing.priced(None)


def test_the_three_costs_of_the_typical_dossier() -> None:
    """Three documents of 50,000 characters on the second model, by hand:
    low: 71,001 tokens seen first at 2.50 (0.1775), 188,002.5 seen again at 0.10 (0.0188),
    4,500 written at 10 (0.045): 0.2413. high: 827,011.5 at 2 (1.654) and 24,000 at 10 (0.24):
    1.894. ceiling: 1,702,024 at 2: 3.404."""
    costs = pricing.costs(dossier(50_000, 50_000, 50_000), SONNET)
    assert costs == pricing.Costs(Decimal("0.24"), Decimal("1.89"), Decimal("3.40"))
    assert costs.low <= costs.high <= costs.ceiling


def test_the_first_model_costs_more_for_the_same_tokens() -> None:
    run = dossier(50_000, 50_000, 50_000)
    assert pricing.costs(run, OPUS).ceiling == Decimal("6.81")
    assert pricing.costs(run, OPUS).high > pricing.costs(run, SONNET).high


def test_nothing_to_read_costs_nothing() -> None:
    assert pricing.costs(estimate.run([]), SONNET) == pricing.Costs(
        Decimal("0.00"), Decimal("0.00"), Decimal("0.00")
    )


@pytest.mark.parametrize(
    ("dollars", "shown"),
    [
        (Fraction(0), "0.00"),
        (Fraction(-1), "0.00"),
        (Fraction(1, 100_000), "0.01"),  # above zero is never shown as free
        (Fraction(1004, 1000), "1.00"),
        (Fraction(1005, 1000), "1.01"),  # half up
        (Fraction(3404048, 1_000_000), "3.40"),
        (Fraction(5421978, 100_000), "54.22"),
    ],
)
def test_amounts_are_rounded_to_the_cent_half_up(dollars: Fraction, shown: str) -> None:
    amount = pricing.rounded(dollars)
    assert amount == Decimal(shown)
    assert amount.as_tuple().exponent == -2  # always two decimals, never a longer exact figure


def test_the_words_shown_with_an_amount_say_it_is_an_estimate() -> None:
    assert pricing.ESTIMATE_NOTE.startswith("Estimates")
    for note in (pricing.ESTIMATE_NOTE, pricing.QUOTA_NOTE):
        assert " - " not in note
        assert "—" not in note
        assert "–" not in note
