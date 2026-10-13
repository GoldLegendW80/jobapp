"""GET /api/profile/export and POST /api/profile/import against a real Postgres: the file format,
the round trip, validation (422 with the path), the size limit (413), stale imports (409),
imports racing a save, isolation, and the optional photo."""

import base64
import json
import struct
import threading
import time
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from PIL import ImageChops, ImageStat
from sqlalchemy.orm import Session, sessionmaker

from app.identity.deps import CSRF_REFUSED
from app.identity.models import User
from app.profile import photo as photos
from app.profile import service
from app.profile.models import Profile, ProfileRevision
from app.profile.photo import normalize
from app.profile.router import STALE, TOO_LARGE
from app.profile.schemas import (
    IMPORT_MAX_BYTES,
    PHOTO_MAX_BYTES,
    SECTION_MODELS,
    SECTIONS,
)
from tests.identity.api_helpers import Api, Clock, make_user
from tests.profile import images
from tests.profile.conftest import ALICE, BOB, FULL, full

FIXTURE = Path(__file__).parent / "fixtures" / "harald_export.json"


def png(width: int = 2, height: int = 2) -> bytes:
    """A real, decodable PNG (grey pixels)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def document(sections: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "format": "jobapp-profile",
        "version": 1,
        "exported_at": "2026-10-08T12:00:00Z",
        "sections": sections if sections is not None else {name: full(name) for name in SECTIONS},
        **extra,
    }


def empty_sections() -> dict[str, Any]:
    return {name: model().model_dump(mode="json") for name, model in SECTION_MODELS.items()}


def export(api: Api, token: str) -> dict[str, Any]:
    response = api.get("/api/profile/export", token=token)
    assert response.status_code == 200, response.text
    return response.json()


def do_import(api: Api, token: str, body: Any, **kwargs: Any) -> Any:
    if isinstance(body, bytes | str):
        return api.post(
            "/api/profile/import",
            token=token,
            content=body,
            headers={"Content-Type": "application/json"},
            **kwargs,
        )
    return api.post("/api/profile/import", token=token, json=body, **kwargs)


def import_ok(api: Api, token: str, body: Any) -> dict[str, Any]:
    response = do_import(api, token, body)
    assert response.status_code == 200, response.text
    return response.json()


def revisions(factory: sessionmaker[Session], email: str | None = None) -> list[ProfileRevision]:
    with factory() as db:
        query = sa.select(ProfileRevision).order_by(ProfileRevision.revision)
        if email is not None:
            query = query.join(User, User.id == ProfileRevision.user_id).where(User.email == email)
        return list(db.execute(query).scalars())


def stored(factory: sessionmaker[Session]) -> list[Profile]:
    with factory() as db:
        return list(db.execute(sa.select(Profile)).scalars())


def canonical(sections: dict[str, Any]) -> str:
    return json.dumps(sections, ensure_ascii=False, indent=2)


# --- export -------------------------------------------------------------------------------------


def test_export_of_an_empty_profile(
    api: Api, alice: str, clock: Clock, factory: sessionmaker[Session]
) -> None:
    response = api.get("/api/profile/export", token=alice)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    day = clock.now.astimezone(UTC).strftime("%Y-%m-%d")
    assert response.headers["content-disposition"] == (f'attachment; filename="profile-{day}.json"')
    body = response.json()
    assert list(body) == ["format", "version", "exported_at", "sections"]  # no photo key
    assert body["format"] == "jobapp-profile"
    assert body["version"] == 1
    assert datetime.fromisoformat(body["exported_at"]) == clock.now
    assert body["sections"] == empty_sections()
    assert list(body["sections"]) == list(SECTIONS)
    # Reading is not saving.
    assert revisions(factory) == []
    assert response.text.endswith("\n")


def test_export_is_indented_utf8_with_the_saved_sections(api: Api, alice: str) -> None:
    identity = {**full("identity"), "full_name": "Zoë Ångström 日本"}
    api.request(
        "PUT", "/api/profile/identity", token=alice, json={"base_revision": 0, "data": identity}
    )
    response = api.get("/api/profile/export", token=alice)
    assert "Zoë Ångström 日本" in response.content.decode("utf-8")  # not \u escapes
    assert response.text.startswith('{\n  "format": "jobapp-profile",')
    body = response.json()
    assert body["sections"]["identity"]["full_name"] == "Zoë Ångström 日本"
    assert body["sections"]["facts"] == {"blocks": []}


def test_export_keys_follow_the_model_order_not_jsonb_order(api: Api, alice: str) -> None:
    import_ok(api, alice, document())
    sections = export(api, alice)["sections"]
    for name in SECTIONS:
        assert list(sections[name]) == list(SECTION_MODELS[name].model_fields)


# --- round trip ---------------------------------------------------------------------------------


def test_round_trip_is_byte_identical(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    for base, section in enumerate(SECTIONS):
        api.request(
            "PUT",
            f"/api/profile/{section}",
            token=alice,
            json={"base_revision": base, "data": full(section)},
        )
    first = export(api, alice)
    assert first["sections"] == FULL
    body = import_ok(api, bob, first)
    assert body["revision"] == len(SECTIONS)
    assert body["completeness"]["overall"] == 100
    second = export(api, bob)
    assert canonical(second["sections"]) == canonical(first["sections"])
    # Importing one's own export again changes nothing but the revision.
    again = import_ok(api, alice, first)
    assert again["revision"] == 2 * len(SECTIONS)
    assert canonical(export(api, alice)["sections"]) == canonical(first["sections"])


def test_round_trip_of_an_empty_profile(api: Api, alice: str, bob: str) -> None:
    first = export(api, alice)
    import_ok(api, bob, first)
    assert canonical(export(api, bob)["sections"]) == canonical(first["sections"])


def year_only_experience() -> dict[str, Any]:
    """Every mix of the two forms: years alone, a year with a month, an open end, no date."""
    return {
        "employers": [
            {
                "key": "northwind",
                "organisation": "Northwind Labs",
                "title": "Engineer",
                "start": "2023",
                "end": None,
                "location": None,
                "contract": None,
            },
            {
                "key": "acme",
                "organisation": "Acme Group",
                "title": "Developer",
                "start": "2021",
                "end": "2023",
                "location": "Lyon",
                "contract": "CDI",
            },
            {
                "key": "clinic",
                "organisation": "Clinic",
                "title": "Intern",
                "start": "2020-09",
                "end": "2021",
                "location": None,
                "contract": "internship",
            },
        ],
        "education": [
            {
                "school": "School of Engineering",
                "degree": "MSc",
                "start": "2019",
                "end": None,
                "location": None,
                "details": None,
            },
            {
                "school": "High school",
                "degree": "Baccalaureate",
                "start": "2016",
                "end": "2019-06",
                "location": None,
                "details": None,
            },
        ],
    }


def test_round_trip_keeps_a_year_alone_character_for_character(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    """Save, export, import into another account, export again: "2021" stays "2021", never
    "2021-01", in the file as written and in every stored copy."""
    saved = api.request(
        "PUT",
        "/api/profile/experience",
        token=alice,
        json={"base_revision": 0, "data": year_only_experience()},
    )
    assert saved.status_code == 200, saved.text
    response = api.get("/api/profile/export", token=alice)
    text = response.content.decode("utf-8")
    assert '"start": "2021",\n          "end": "2023",' in text
    assert '"start": "2020-09",\n          "end": "2021",' in text
    assert '"start": "2019",\n          "end": null,' in text
    assert "2021-01" not in text
    assert "2023-12" not in text
    first = response.json()
    assert first["sections"]["experience"] == year_only_experience()

    import_ok(api, bob, first)
    second = api.get("/api/profile/export", token=bob)
    assert canonical(second.json()["sections"]) == canonical(first["sections"])
    assert second.json()["sections"]["experience"] == year_only_experience()
    # The copies in the database: the profile row and the revision of each account.
    assert [profile.sections["experience"] for profile in stored(factory)] == [
        year_only_experience()
    ] * 2
    kept = [row.data for row in revisions(factory) if row.section == "experience"]
    assert kept == [year_only_experience()] * 2


def test_an_import_file_with_year_only_dates(api: Api, alice: str) -> None:
    """A file written by hand or by an agent, never exported by this app."""
    sections = empty_sections()
    sections["experience"] = year_only_experience()
    body = import_ok(api, alice, document(sections))
    assert body["sections"]["experience"] == year_only_experience()
    assert body["completeness"]["sections"]["experience"] == {"percent": 100, "missing": []}
    read = api.get("/api/profile", token=alice).json()
    assert read["sections"]["experience"] == year_only_experience()
    past = api.get(f"/api/profile/revisions/{SECTIONS.index('experience') + 1}", token=alice)
    assert past.json()["data"] == year_only_experience()


def test_import_writes_one_revision_per_section_in_order(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    api.request(
        "PUT", "/api/profile/facts", token=alice, json={"base_revision": 0, "data": full("facts")}
    )
    body = import_ok(api, alice, document())
    assert body["revision"] == 1 + len(SECTIONS)
    assert body["sections"] == FULL
    rows = revisions(factory)
    assert [(r.revision, r.section) for r in rows[1:]] == [
        (2 + index, name) for index, name in enumerate(SECTIONS)
    ]
    assert all(r.data == FULL[r.section] and r.created_at == clock.now for r in rows[1:])
    # Each revision is readable and restorable like a normal save.
    listed = api.get("/api/profile/revisions?section=answers", token=alice).json()
    assert [item["revision"] for item in listed["items"]] == [1 + len(SECTIONS)]


def test_import_normalises_like_a_save(api: Api, alice: str) -> None:
    sections = {name: full(name) for name in SECTIONS}
    sections["identity"]["full_name"] = "  Ada Example  "
    sections["identity"]["email"] = "ADA@Example.ORG"
    sections["identity"]["website"] = ""
    body = import_ok(api, alice, document(sections))
    identity = body["sections"]["identity"]
    assert identity["full_name"] == "Ada Example"
    assert identity["email"] == "ada@example.org"
    assert identity["website"] is None


def test_harald_fixture_imports_complete(api: Api, alice: str) -> None:
    """The checked-in golden output of the importer (photo left out) is a valid import."""
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    golden.pop("photo")
    body = import_ok(api, alice, golden)
    assert body["completeness"]["overall"] == 100, body["completeness"]
    assert canonical(export(api, alice)["sections"]) == canonical(golden["sections"])


# --- refused documents --------------------------------------------------------------------------


def assert_untouched(factory: sessionmaker[Session]) -> None:
    assert revisions(factory) == []
    assert all(profile.revision == 0 for profile in stored(factory))


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        (
            lambda d: d["sections"]["identity"].update(email="not an email"),
            "sections.identity.email: must be a valid email address",
        ),
        (
            lambda d: d["sections"]["experience"]["employers"][0].update(start="2025-13"),
            "sections.experience.employers.0.start: must be a year or a month, written YYYY or "
            "YYYY-MM, between 1900 and 2100",
        ),
        (
            lambda d: d["sections"]["experience"]["education"][0].update(end="2025-8"),
            "sections.experience.education.0.end: must be a year or a month, written YYYY or "
            "YYYY-MM, between 1900 and 2100",
        ),
        (
            lambda d: d["sections"]["experience"]["employers"][1].update(start="2023", end="2022"),
            "sections.experience.employers.1: end must not be before start",
        ),
        (
            lambda d: d["sections"]["experience"]["employers"][0].update(start=2025),
            "sections.experience.employers.0.start: Input should be a valid string",
        ),
        (
            lambda d: d["sections"]["search"]["title_exclude"].update(terms=["(unclosed"]),
            "sections.search.title_exclude.terms: term 1: invalid regular expression at "
            "position 0: missing ), unterminated subpattern",
        ),
        (
            lambda d: d["sections"]["skills"].update(years_experience="3"),
            "sections.skills.years_experience: Input should be a valid number",
        ),
        (lambda d: d["sections"].pop("answers"), "sections.answers: Field required"),
        (
            lambda d: d["sections"].update(photo={}),
            "sections.photo: Extra inputs are not permitted",
        ),
        (lambda d: d.update(owner="bob"), "owner: Extra inputs are not permitted"),
        (lambda d: d.update(exported_at="yesterday"), "exported_at: Input should be a valid"),
        (lambda d: d.update(base_revision=-1), "base_revision: Input should be greater than"),
        (lambda d: d.update(base_revision="0"), "base_revision: Input should be a valid integer"),
        (lambda d: d.update(sections=[]), "sections: Input should be a valid dictionary"),
    ],
)
def test_invalid_document_is_422_with_the_path(
    api: Api, alice: str, factory: sessionmaker[Session], change: Any, detail: str
) -> None:
    body = document()
    change(body)
    response = do_import(api, alice, body)
    assert response.status_code == 422
    assert response.json()["detail"].startswith(detail)
    assert "not an email" not in response.text  # the value is never echoed
    assert_untouched(factory)


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        (lambda d: d.update(format="something-else"), "Unknown file format"),
        (lambda d: d.pop("format"), "Unknown file format"),
        (lambda d: d.update(format=None), "Unknown file format"),
        (lambda d: d.update(version=2), "Unsupported export version"),
        (lambda d: d.update(version="1"), "Unsupported export version"),
        (lambda d: d.update(version=True), "Unsupported export version"),
        (lambda d: d.update(version=1.0), "Unsupported export version"),
        (lambda d: d.pop("version"), "Unsupported export version"),
    ],
)
def test_unknown_format_or_version_is_422(
    api: Api, alice: str, factory: sessionmaker[Session], change: Any, detail: str
) -> None:
    body = document()
    change(body)
    response = do_import(api, alice, body)
    assert response.status_code == 422
    assert response.json()["detail"].startswith(detail)
    assert_untouched(factory)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b'{"format": "jobapp-profile",}',
        b"\xff\xfe\x00garbage",
        b"[" * 100_000 + b"]" * 100_000,
    ],
    ids=["empty", "unclosed", "trailing-comma", "bad-bytes", "deep-nesting"],
)
def test_malformed_json_is_422(
    api: Api, alice: str, factory: sessionmaker[Session], raw: bytes
) -> None:
    response = do_import(api, alice, raw)
    assert response.status_code == 422
    assert response.json() == {"detail": "The import file is not valid JSON."}
    assert_untouched(factory)


@pytest.mark.parametrize("raw", [b"[]", b'"jobapp-profile"', b"1", b"null", b"NaN"])
def test_json_that_is_not_an_object_is_422(api: Api, alice: str, raw: bytes) -> None:
    response = do_import(api, alice, raw)
    assert response.status_code == 422
    assert response.json() == {"detail": "The import file must hold one JSON object."}


def padded(size: int) -> bytes:
    """A valid import document of exactly `size` bytes (trailing spaces are valid JSON)."""
    raw = json.dumps(document()).encode()
    return raw + b" " * (size - len(raw))


def test_two_megabytes_is_accepted(api: Api, alice: str) -> None:
    raw = padded(IMPORT_MAX_BYTES)
    assert len(raw) == 2 * 1024 * 1024
    assert do_import(api, alice, raw).status_code == 200


def test_two_megabytes_and_one_byte_is_413(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    response = do_import(api, alice, padded(IMPORT_MAX_BYTES + 1))
    assert response.status_code == 413
    assert response.json() == {"detail": TOO_LARGE}
    assert_untouched(factory)


def test_a_streamed_body_is_cut_at_the_limit(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    sent = []

    def chunks() -> Iterator[bytes]:
        for _ in range(64):  # 4 MB in 64 KB chunks, no Content-Length
            sent.append(1)
            yield b" " * 65_536

    response = api.post(
        "/api/profile/import",
        token=alice,
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.json() == {"detail": TOO_LARGE}
    assert_untouched(factory)


def test_a_declared_length_over_the_limit_is_refused_before_reading(api: Api, alice: str) -> None:
    response = api.post(
        "/api/profile/import",
        token=alice,
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "999999999"},
    )
    # The declared length is refused at once; the server never waits for the missing bytes.
    assert response.status_code == 413


# --- concurrency --------------------------------------------------------------------------------


def test_stale_base_revision_is_409_with_the_current_profile(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    api.request(
        "PUT", "/api/profile/facts", token=alice, json={"base_revision": 0, "data": full("facts")}
    )
    response = do_import(api, alice, document(base_revision=0))
    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == STALE
    assert body["revision"] == 1
    assert body["sections"]["facts"] == FULL["facts"]
    assert body["sections"]["identity"] is None
    assert len(revisions(factory)) == 1
    assert response.headers["cache-control"] == "no-store"
    # With the current revision it goes through.
    assert import_ok(api, alice, document(base_revision=1))["revision"] == 1 + len(SECTIONS)


def test_base_revision_of_a_new_profile_is_zero(api: Api, alice: str) -> None:
    assert import_ok(api, alice, document(base_revision=0))["revision"] == len(SECTIONS)


def _import_while_a_save_holds_the_lock(
    api: Api, token: str, factory: sessionmaker[Session], email: str, body: dict[str, Any]
) -> Any:
    """Open a save in another session (row locked, not committed), start the import, check it
    waits on the lock, then commit the save and return the import's response."""
    with factory() as db:
        user_id = db.execute(
            sa.text("SELECT id FROM users WHERE email = :e"), {"e": email}
        ).scalar_one()
    holder = factory()
    service.save_section(holder, user_id, "facts", FULL["facts"], 0, datetime.now(UTC))
    result: dict[str, Any] = {}
    worker = threading.Thread(target=lambda: result.update(response=do_import(api, token, body)))
    worker.start()
    time.sleep(0.5)
    assert worker.is_alive(), "the import did not wait for the save in progress"
    holder.commit()
    holder.close()
    worker.join(timeout=30)
    assert not worker.is_alive()
    return result["response"]


def test_import_racing_a_save_with_base_revision_is_409(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    api.get("/api/profile", token=alice)
    response = _import_while_a_save_holds_the_lock(
        api, alice, factory, ALICE, document(base_revision=0)
    )
    assert response.status_code == 409
    assert response.json()["revision"] == 1
    assert [r.section for r in revisions(factory)] == ["facts"]


def test_import_racing_a_save_without_base_revision_is_serialised(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    api.get("/api/profile", token=alice)
    response = _import_while_a_save_holds_the_lock(api, alice, factory, ALICE, document())
    assert response.status_code == 200
    assert response.json()["revision"] == 1 + len(SECTIONS)
    assert [r.revision for r in revisions(factory)] == list(range(1, 2 + len(SECTIONS)))
    assert response.json()["sections"] == FULL


# --- isolation ----------------------------------------------------------------------------------


def test_import_touches_only_the_caller(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    api.request(
        "PUT", "/api/profile/facts", token=bob, json={"base_revision": 0, "data": full("facts")}
    )
    bob_before = export(api, bob)
    import_ok(api, alice, document(photo={"mime": "image/png", "base64": b64(png())}))
    bob_after = export(api, bob)
    assert bob_after["sections"] == bob_before["sections"]
    assert "photo" not in bob_after
    assert [r.section for r in revisions(factory, BOB)] == ["facts"]
    assert len(revisions(factory, ALICE)) == len(SECTIONS)
    # And alice's export holds only her data.
    assert export(api, alice)["sections"] == FULL


def test_import_needs_the_csrf_header(api: Api, alice: str, factory: sessionmaker[Session]) -> None:
    response = do_import(api, alice, document(), csrf=False)
    assert response.status_code == 403
    assert response.json() == {"detail": CSRF_REFUSED}
    assert_untouched(factory)


def test_export_needs_no_csrf_header(api: Api, alice: str) -> None:
    assert api.get("/api/profile/export", token=alice, csrf=False).status_code == 200


# --- photo --------------------------------------------------------------------------------------


def photo_row(factory: sessionmaker[Session]) -> tuple[bytes | None, str | None]:
    with factory() as db:
        row = db.execute(
            sa.select(Profile.photo, Profile.photo_mime)
            .join(User, User.id == Profile.user_id)
            .where(User.email == ALICE)
        ).one_or_none()
        return (None, None) if row is None else (row[0], row[1])


@pytest.mark.parametrize(("image", "mime"), [(png(), "image/png"), (images.jpeg(), "image/jpeg")])
def test_import_with_a_photo_stores_it_re_encoded_and_exports_it(
    api: Api, alice: str, factory: sessionmaker[Session], image: bytes, mime: str
) -> None:
    import_ok(api, alice, document(photo={"mime": mime, "base64": b64(image)}))
    # Stored as an upload would be: through photo.normalize, not as received.
    expected = normalize(image).data
    assert photo_row(factory) == (expected, mime)
    exported = export(api, alice)
    assert exported["photo"] == {"mime": mime, "base64": b64(expected)}
    # The photo is not a section: no revision for it.
    assert len(revisions(factory)) == len(SECTIONS)


def test_imported_photo_is_stripped_and_turned_upright(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    source = images.jpeg_with_gps(orientation=6)
    assert "exif" in images.metadata(source)
    import_ok(api, alice, document(photo={"mime": "image/jpeg", "base64": b64(source)}))
    data, mime = photo_row(factory)
    assert data is not None
    assert mime == "image/jpeg"
    assert images.metadata(data) == {}
    assert images.opened(data).size == (30, 40)
    assert b"Ada Example" not in data
    assert b"secret comment" not in data


def test_import_of_a_photo_that_does_not_decode_is_422(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    truncated = images.jpeg(400, 300)[:-200]
    response = do_import(
        api, alice, document(photo={"mime": "image/jpeg", "base64": b64(truncated)})
    )
    assert response.status_code == 422
    assert response.json() == {"detail": f"photo: {photos.UNREADABLE}"}
    assert_untouched(factory)
    assert photo_row(factory) == (None, None)


def test_import_without_a_photo_keeps_the_stored_one(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    expected = normalize(png()).data
    import_ok(api, alice, document(photo={"mime": "image/png", "base64": b64(png())}))
    import_ok(api, alice, document())
    assert photo_row(factory) == (expected, "image/png")
    import_ok(api, alice, document(photo=None))
    assert photo_row(factory) == (expected, "image/png")


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg"])
def test_round_trip_with_a_photo(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session], mime: str
) -> None:
    """Export then import is identical for the sections; the photo is re-encoded on import, so
    a PNG (lossless) comes back byte for byte and a JPEG as the same picture: same type and
    size, pixels within re-compression noise."""
    upload = images.png(300, 200) if mime == "image/png" else images.jpeg(300, 200)
    for base, section in enumerate(SECTIONS):
        api.request(
            "PUT",
            f"/api/profile/{section}",
            token=alice,
            json={"base_revision": base, "data": full(section)},
        )
    assert api.request("PUT", "/api/profile/photo", token=alice, content=upload).status_code == 200
    first = export(api, alice)
    import_ok(api, bob, first)
    second = export(api, bob)
    assert canonical(second["sections"]) == canonical(first["sections"])
    assert second["photo"]["mime"] == first["photo"]["mime"] == mime
    before = images.opened(base64.b64decode(first["photo"]["base64"]))
    after = images.opened(base64.b64decode(second["photo"]["base64"]))
    assert after.size == before.size == (300, 200)
    if mime == "image/png":
        assert second["photo"] == first["photo"]
    else:
        difference = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
        assert sum(ImageStat.Stat(difference).mean) / 3 < 2


@pytest.mark.parametrize(
    ("photo", "detail"),
    [
        ({"mime": "image/svg+xml", "base64": b64(SVG)}, "photo.mime: Input should be"),
        ({"mime": "image/png", "base64": b64(SVG)}, "photo: only PNG and JPEG images"),
        ({"mime": "image/png", "base64": b64(png())[:-1]}, "photo: base64 is invalid"),
        ({"mime": "image/png", "base64": b64(png())[:-2] + "!="}, "photo: base64 is invalid"),
        ({"mime": "image/png", "base64": "data:image/png;base64,"}, "photo: base64 is invalid"),
        ({"mime": "image/png", "base64": ""}, "photo.base64: String should have at least"),
        ({"mime": "image/png", "base64": b64(JPEG)}, "photo: the image is a image/jpeg"),
        ({"mime": "image/jpeg", "base64": b64(b"GIF89a" + b"\x00" * 20)}, "photo: only PNG"),
        ({"mime": "image/png"}, "photo.base64: Field required"),
        ({"mime": "image/png", "base64": b64(png()), "name": "x.png"}, "photo.name: Extra"),
        ("iVBORw0KGgo=", "photo: Input should be a valid dictionary"),
    ],
)
def test_bad_photo_is_422(
    api: Api, alice: str, factory: sessionmaker[Session], photo: Any, detail: str
) -> None:
    response = do_import(api, alice, document(photo=photo))
    assert response.status_code == 422
    assert response.json()["detail"].startswith(detail), response.json()
    assert_untouched(factory)
    assert photo_row(factory) == (None, None)


def test_photo_over_two_megabytes_is_refused(api: Api, alice: str) -> None:
    image = png() + b"\x00" * (PHOTO_MAX_BYTES + 1 - len(png()))
    # The whole document is then over the import limit too: refused before decoding.
    response = do_import(api, alice, document(photo={"mime": "image/png", "base64": b64(image)}))
    assert response.status_code == 413


def test_photo_size_check_without_the_body_limit() -> None:
    from app.profile.schemas import decode_photo

    image = png() + b"\x00" * (PHOTO_MAX_BYTES + 1 - len(png()))
    with pytest.raises(ValueError, match="larger than 2 MB"):
        decode_photo("image/png", b64(image))
    with pytest.raises(ValueError, match="empty"):
        decode_photo("image/png", "")


def test_service_import_needs_all_six_sections(factory: sessionmaker[Session]) -> None:
    user = make_user(factory, email=ALICE)
    partial = {name: FULL[name] for name in SECTIONS[:-1]}
    with factory() as db, pytest.raises(ValueError, match="exactly the six sections"):
        service.import_profile(db, user.id, partial, None, None, datetime.now(UTC))
