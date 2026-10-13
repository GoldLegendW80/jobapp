"""What the tokens of an estimate may cost, for a user whose credential is an Anthropic API key.
A subscription is quota, not money: no amount is ever computed for it.

**The price table is a dated copy, not a live feed.** `PRICES` holds the list prices per million
tokens of every model a run can use, `PRICES_AS_OF` the day they were read and `PRICES_SOURCE`
where. To keep it current: read the pricing page, change the figures and the date together, in
one commit. The answer of the estimate route carries the date, so the page shows how old the
figures are, and `tests/ingestion/test_pricing.py` fails when a model can be chosen by a run or
by any registered agent and has no price here.

The three costs of an estimate ("Tokens", cost):

- **low**: the first appearance of each input token at the cache write price (1.25 times the
  input price, assumption A14), every later appearance at the cache read price, plus the low
  output at the output price;
- **high**: every input token of `H` at the full input price, plus the high output;
- **ceiling**: every token of `C` at the full input price, as the plan quotes it. It is the
  price of the token ceiling counted in input tokens, **not a bound on money**: what a run
  spends under `C` includes output tokens (five times the input price) and cache writes (1.25
  times), so a run that reaches `C` can cost more than this amount. Nothing shown to a user
  may call it a maximum.

Amounts are US dollars rounded to the cent, half up, and an amount above zero is never shown as
0.00 (it becomes 0.01). They are estimates from list prices: the provider's invoice is the only
exact figure, and the words shown with them say so.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from app.agents.models import CredentialKind
from app.ingestion.estimate import RunEstimate

# Read on 2026-10-13 from https://platform.claude.com/docs/en/about-claude/pricing, the table
# "Model pricing": base input, 5 minute cache write, cache hit, output. A cache hit is 0.05 times
# the base input price of its own model, so it is not the same figure for both models.
PRICES_AS_OF = date(2026, 10, 13)
PRICES_SOURCE = "Anthropic list prices for the Claude API, first party, standard speed"
PRICES_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
CURRENCY = "USD"
CACHE_WRITE_FACTOR = Fraction(5, 4)  # a 5 minute cache write (A14)
PER = 1_000_000
CENT = Decimal("0.01")
ESTIMATE_NOTE = (
    f"Estimates from list prices read on {PRICES_AS_OF.isoformat()}, rounded to the cent. "
    "Your provider's invoice is the exact figure."
)
QUOTA_NOTE = "On a Claude subscription, tokens count against your quota, not as money."


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: Fraction
    output: Fraction
    cache_read: Fraction

    @property
    def cache_write(self) -> Fraction:
        return self.input * CACHE_WRITE_FACTOR


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(Fraction(4), Fraction(20), Fraction(1, 5)),
    "claude-sonnet-5-5": Price(Fraction(2), Fraction(10), Fraction(1, 10)),
}


@dataclass(frozen=True)
class Costs:
    """Three estimates in US dollars. `ceiling` prices the token ceiling as input tokens: it is
    not the most a run can cost (module docstring)."""

    low: Decimal
    high: Decimal
    ceiling: Decimal


def priced(kind: CredentialKind | str | None) -> bool:
    """Only an API key is billed per token."""
    return kind is not None and CredentialKind(kind) is CredentialKind.anthropic_api_key


def price_of(model: str) -> Price:
    try:
        return PRICES[model]
    except KeyError:
        raise LookupError(f"no price for the model {model}") from None


def rounded(dollars: Fraction) -> Decimal:
    """To the cent, half up; above zero never 0.00."""
    if dollars <= 0:
        return Decimal("0.00")
    amount = (Decimal(dollars.numerator) / Decimal(dollars.denominator)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    return max(amount, CENT)


def costs(estimate: RunEstimate, model: str) -> Costs:
    """The three costs of an estimate on `model` (module docstring)."""
    price = price_of(model)
    again = estimate.low_input - estimate.low_fresh
    low = (
        estimate.low_fresh * price.cache_write
        + again * price.cache_read
        + estimate.low_output * price.output
    )
    high = estimate.high_input * price.input + estimate.high_output * price.output
    ceiling = estimate.ceiling * price.input
    return Costs(low=rounded(low / PER), high=rounded(high / PER), ceiling=rounded(ceiling / PER))
