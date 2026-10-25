"""Numeric pay readers, before fractional writers and storage are enabled."""

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from app.jobs import router, service
from app.jobs.models import Job, JobStatus
from app.jobs.schemas import JobDetail, JobListItem, PayIn
from app.opportunities.pay import pay_text


def posting(**overrides: object) -> dict:
    return {
        "id": uuid.UUID("00000000-0000-4000-9000-000000000550"),
        "slug": "example-pay",
        "company": "Example Robotics",
        "title": "Research intern",
        "location": "Paris",
        "contract_type": "Stage",
        "url": None,
        "source": None,
        "status": "found",
        "fit": None,
        "fit_overridden": False,
        "competencies": [],
        "gaps": [],
        "description": None,
        "published_on": None,
        "found_on": date(2026, 10, 23),
        "applied_on": None,
        "notes": "",
        "file_count": 0,
        "created_at": datetime(2026, 10, 23, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 23, tzinfo=UTC),
        "pay_min": Decimal("4.35"),
        "pay_max": Decimal("5.10"),
        "pay_currency": "EUR",
        "pay_period": "hour",
        **overrides,
    }


@pytest.mark.parametrize(
    "amount",
    [
        1,
        45000,
        10000000,
        Decimal("4.35"),
        Decimal("16.5"),
        Decimal("45000.00"),
        Decimal("4.3500"),
        4.35,
        16.5,
        45000.0,
    ],
)
def test_response_pay_round_trips_as_numbers(amount):
    item = JobListItem(**posting(pay_min=amount, pay_max=amount))
    if isinstance(amount, Decimal):
        assert isinstance(item.pay_min, Decimal)
    data = json.loads(item.model_dump_json())
    assert type(data["pay_min"]) in (int, float)
    assert Decimal(str(data["pay_min"])) == Decimal(str(amount))
    assert JobListItem.model_validate_json(item.model_dump_json()).pay_min == item.pay_min
    if Decimal(str(amount)) == int(amount):
        assert type(data["pay_min"]) is int


@pytest.mark.parametrize(
    "amount",
    [
        True,
        False,
        "4.35",
        "45000",
        -1,
        0,
        Decimal("0.99"),
        Decimal("10000000.01"),
        Decimal("4.351"),
        4.351,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        float("nan"),
        float("inf"),
        [],
        {},
    ],
)
def test_response_pay_refuses_invalid_amounts(amount):
    with pytest.raises(ValidationError):
        JobListItem(**posting(pay_min=amount, pay_max=amount))


def test_response_refuses_reversed_range():
    with pytest.raises(ValidationError):
        JobListItem(**posting(pay_min=Decimal("5.10"), pay_max=Decimal("4.35")))


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_pay_response_schema_is_bounded_numeric_only(mode):
    for schema in (JobListItem, JobDetail):
        field = schema.model_json_schema(mode=mode)["properties"]["pay_min"]
        assert field["anyOf"] == [
            {"type": "number", "minimum": 1, "maximum": 10000000, "multipleOf": 0.01},
            {"type": "null"},
        ]


def test_actual_detail_construction_keeps_decimal_until_json(monkeypatch):
    data = posting()
    del data["file_count"]
    job = Job(**data, posting_text="Invented posting.", legacy={})
    monkeypatch.setattr(service, "list_files", lambda *args: [])
    monkeypatch.setattr(service, "recent_events", lambda *args: ([], 0))
    detail = router._detail(None, SimpleNamespace(id=uuid.uuid4()), job)
    assert detail.pay_min == Decimal("4.35")
    assert isinstance(detail.pay_min, Decimal)
    assert isinstance(detail.model_dump()["pay_min"], Decimal)
    assert jsonable_encoder(detail)["pay_min"] == 4.35
    assert jsonable_encoder(detail)["pay_max"] == 5.1


def test_null_and_omitted_pay_keep_existing_response_semantics():
    data = posting(pay_min=None, pay_max=None, pay_currency=None, pay_period=None)
    assert JobListItem(**data).model_dump(mode="json")["pay_min"] is None
    for name in ("pay_min", "pay_max", "pay_currency", "pay_period"):
        del data[name]
    item = JobListItem(**data)
    assert item.model_dump(exclude_unset=True).get("pay_min") is None
    assert "pay_min" not in item.model_fields_set


def test_edit_event_values_json_encode_as_numbers():
    values = {
        "changes": {
            "pay_min": {
                "from": service._jsonable(Decimal("4.35")),
                "to": service._jsonable(Decimal("16.50")),
            },
            "pay_max": {
                "from": service._jsonable(Decimal("45000.00")),
                "to": service._jsonable(None),
            },
            "status": {"from": service._jsonable(JobStatus.found)},
        },
    }
    encoded = json.loads(json.dumps(values, allow_nan=False))
    assert encoded["changes"]["pay_min"] == {"from": 4.35, "to": 16.5}
    assert type(encoded["changes"]["pay_max"]["from"]) is int
    assert encoded["changes"]["status"]["from"] == "found"
    assert encoded["changes"]["pay_max"]["to"] is None


@pytest.mark.parametrize(
    ("low", "high", "currency", "period", "text"),
    [
        ("4.35", "5.10", "EUR", "hour", "4.35 to 5.10 EUR an hour"),
        ("16.50", "16.50", "USD", "hour", "16.50 USD an hour"),
        ("600.50", "600.50", "CHF", "day", "600.50 CHF a day"),
        ("1200.50", "1200.50", "GBP", "month", "1,200.50 GBP a month"),
        ("120000.75", "120000.75", "CAD", "year", "120,000.75 CAD a year"),
        ("45000.00", "55000.00", "EUR", "year", "45,000 to 55,000 EUR a year"),
        ("10000000.00", "10000000.00", "EUR", "year", "10,000,000 EUR a year"),
    ],
)
def test_backend_pay_words_preserve_cents_and_integer_wording(low, high, currency, period, text):
    assert pay_text(Decimal(low), Decimal(high), currency, period) == text


@pytest.mark.parametrize("amount", [4.35, Decimal("4.35"), 16.5, "4.35"])
def test_request_writer_stays_whole_only(amount):
    with pytest.raises(ValidationError):
        PayIn(min=amount, max=amount, currency="EUR", period="hour")
