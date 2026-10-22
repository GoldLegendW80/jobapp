"""Request schemas fit their body class without weakening typed approval/callback contracts."""

import uuid

import pytest
from pydantic import ValidationError

from app.apply.approval_router import ApproveIn as ApplicationApproval
from app.apply.profile_schemas import ApproveIn as ProfileApproval
from app.body_limit import DEFAULT_MAX_BYTES
from app.browser.later_router import Reference
from app.mail.send_router import SendIn
from tests.identity.test_body_limit_api import schema_bound


@pytest.mark.parametrize("model", [ApplicationApproval, ProfileApproval, Reference, SendIn])
def test_each_approval_and_callback_schema_is_finite_and_fits_the_default_class(model):
    schema = model.model_json_schema()
    size, bounded = schema_bound(schema, schema.get("$defs", {}))
    assert bounded
    assert size <= DEFAULT_MAX_BYTES


@pytest.mark.parametrize(
    ("model", "body", "field", "limit"),
    [
        (ApplicationApproval, {"answers_hash": "a" * 64}, "acknowledged_email_issues", 64),
        (SendIn, {"content_hash": "a" * 64}, "acknowledged_issues", 64),
        (SendIn, {"content_hash": "a" * 64}, "confirmed_new_recipients", 254),
    ],
)
def test_list_members_accept_the_boundary_and_refuse_a_single_oversized_member(
    model, body, field, limit
):
    # Four-byte Unicode probes the byte estimate as well as the character limit.
    value = "\U0001f4bc" * limit
    assert getattr(model.model_validate({**body, field: [value]}), field) == [value]
    with pytest.raises(ValidationError) as caught:
        model.model_validate({**body, field: [value + "x"]})
    assert caught.value.errors()[0]["loc"] == (field, 0)


@pytest.mark.parametrize(
    ("model", "body", "field", "count"),
    [
        (ApplicationApproval, {"answers_hash": "a" * 64}, "acknowledged_email_issues", 1000),
        (SendIn, {"content_hash": "a" * 64}, "acknowledged_issues", 1000),
        (SendIn, {"content_hash": "a" * 64}, "confirmed_new_recipients", 20),
    ],
)
def test_decision_array_counts_are_preserved(model, body, field, count):
    assert len(getattr(model.model_validate({**body, field: ["x"] * count}), field)) == count
    with pytest.raises(ValidationError):
        model.model_validate({**body, field: ["x"] * (count + 1)})


@pytest.mark.parametrize("value", ["a" * 65, "A" * 64, "a" * 63, "a" * 64 + "\n"])
def test_profile_hash_still_requires_exact_lowercase_sha256(value):
    assert ProfileApproval(values_hash="a" * 64).values_hash == "a" * 64
    with pytest.raises(ValidationError):
        ProfileApproval(values_hash=value)


@pytest.mark.parametrize(
    "module", ["a" * 41, "Application", "application.x", {"name": "application"}]
)
def test_later_reference_still_refuses_unregistered_module_shapes(module):
    with pytest.raises(ValidationError):
        Reference(module=module, ref=uuid.uuid4())


def test_later_reference_accepts_the_full_registered_name_shape_and_uuid():
    ref = uuid.uuid4()
    value = Reference(module="a" * 40, ref=str(ref))
    assert value.module == "a" * 40
    assert value.ref == ref
