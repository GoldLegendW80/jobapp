"""PUT, GET and DELETE /api/profile/photo against a real Postgres: upload, re-encoding, caching
with ETag and 304, size and type refusals, idempotent delete, CSRF, anonymous access, and
isolation between users."""

import hashlib
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.identity.deps import CSRF_REFUSED, UNAUTHORIZED
from app.identity.models import User
from app.profile import photo as photos
from app.profile import service
from app.profile.models import Profile, ProfileRevision
from app.profile.router import NO_PHOTO, PHOTO_CACHE
from app.profile.schemas import PHOTO_MAX_BYTES
from tests.identity.api_helpers import Api, Clock, make_user
from tests.profile import images
from tests.profile.conftest import ALICE, BOB, full

URL = "/api/profile/photo"


def put(api: Api, token: str | None, data: bytes | Iterator[bytes], **kwargs: Any) -> Any:
    return api.request("PUT", URL, token=token, content=data, **kwargs)


def upload(api: Api, token: str, data: bytes, **kwargs: Any) -> dict[str, Any]:
    response = put(api, token, data, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def get(api: Api, token: str | None, **kwargs: Any) -> httpx.Response:
    return api.get(URL, token=token, **kwargs)


def delete(api: Api, token: str | None, **kwargs: Any) -> httpx.Response:
    return api.request("DELETE", URL, token=token, **kwargs)


def row(factory: sessionmaker[Session], email: str) -> tuple[bytes | None, str | None] | None:
    with factory() as db:
        found = db.execute(
            sa.select(Profile.photo, Profile.photo_mime)
            .join(User, User.id == Profile.user_id)
            .where(User.email == email)
        ).one_or_none()
        return None if found is None else (found[0], found[1])


def profile(api: Api, token: str) -> dict[str, Any]:
    response = api.get("/api/profile", token=token)
    assert response.status_code == 200
    return response.json()


def iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


# --- upload ---------------------------------------------------------------------------------------


def test_upload_png_then_get_it(api: Api, alice: str, factory: sessionmaker[Session]) -> None:
    source = images.png_with_text_and_alpha()
    response = put(api, alice, source)
    assert response.status_code == 200
    body = response.json()
    stored = photos.normalize(source).data
    etag = f'"{hashlib.sha256(stored).hexdigest()}"'
    assert body == {
        "mime": "image/png",
        "width": 40,
        "height": 30,
        "size": len(stored),
        "etag": etag,
        "updated_at": body["updated_at"],
    }
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == "no-store"
    assert row(factory, ALICE) == (stored, "image/png")

    got = get(api, alice)
    assert got.status_code == 200
    assert got.content == stored
    assert got.headers["content-type"] == "image/png"
    assert got.headers["cache-control"] == PHOTO_CACHE == "private, no-cache"
    assert got.headers["etag"] == etag
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["content-disposition"] == 'inline; filename="photo.png"'
    assert images.metadata(got.content) == {}


def test_upload_jpeg_with_gps_is_upright_and_stripped(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    body = upload(api, alice, images.jpeg_with_gps(orientation=6))
    assert (body["mime"], body["width"], body["height"]) == ("image/jpeg", 30, 40)
    got = get(api, alice)
    assert got.headers["content-type"] == "image/jpeg"
    assert got.headers["content-disposition"] == 'inline; filename="photo.jpg"'
    image = images.opened(got.content)
    assert image.size == (30, 40)
    assert images.metadata(got.content) == {}
    assert image.getexif().get_ifd(images.GPS_IFD) == {}
    for secret in (b"GPS", b"SecretCam", b"Ada Example", b"Bordeaux", b"secret comment"):
        assert secret not in got.content


def test_large_photo_is_downscaled(api: Api, alice: str) -> None:
    body = upload(api, alice, images.save(images.halves(2048, 1536), "JPEG", quality=90))
    assert (body["width"], body["height"]) == (1024, 768)
    assert images.opened(get(api, alice).content).size == (1024, 768)


@pytest.mark.parametrize("content_type", ["image/jpeg", "application/octet-stream", "text/plain"])
def test_type_comes_from_the_bytes_not_the_headers(
    api: Api, alice: str, factory: sessionmaker[Session], content_type: str
) -> None:
    """A PNG sent as image/jpeg named photo.jpg is a PNG."""
    body = upload(
        api,
        alice,
        images.png(),
        headers={
            "Content-Type": content_type,
            "Content-Disposition": 'attachment; filename="photo.jpg"',
        },
    )
    assert body["mime"] == "image/png"
    assert row(factory, ALICE)[1] == "image/png"  # type: ignore[index]


def test_upload_replaces_the_photo(api: Api, alice: str, factory: sessionmaker[Session]) -> None:
    first = upload(api, alice, images.png())
    second = upload(api, alice, images.jpeg())
    assert first["etag"] != second["etag"]
    assert row(factory, ALICE)[1] == "image/jpeg"  # type: ignore[index]
    assert get(api, alice).headers["etag"] == second["etag"]


def test_photo_is_not_a_revision_but_moves_updated_at(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    api.request(
        "PUT", "/api/profile/facts", token=alice, json={"base_revision": 0, "data": full("facts")}
    )
    before = profile(api, alice)
    moved = clock.advance(minutes=5)
    body = upload(api, alice, images.png())
    assert body["updated_at"] == iso(moved)
    after = profile(api, alice)
    assert after["revision"] == before["revision"] == 1
    assert after["updated_at"] == iso(moved)
    assert after["sections"] == before["sections"]
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(ProfileRevision)) == 1


def test_upload_on_a_user_without_a_profile_row_creates_it(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    assert row(factory, ALICE) is None
    upload(api, alice, images.png())
    body = profile(api, alice)
    assert body["revision"] == 0
    assert body["has_photo"] is True


# --- GET /api/profile carries has_photo and photo_etag ------------------------------------------


def test_profile_reports_the_photo(api: Api, alice: str) -> None:
    empty = profile(api, alice)
    assert (empty["has_photo"], empty["photo_etag"]) == (False, None)
    body = upload(api, alice, images.png())
    with_photo = profile(api, alice)
    assert with_photo["has_photo"] is True
    assert with_photo["photo_etag"] == body["etag"] == get(api, alice).headers["etag"]
    assert delete(api, alice).status_code == 204
    gone = profile(api, alice)
    assert (gone["has_photo"], gone["photo_etag"]) == (False, None)


def test_section_save_and_import_answers_carry_the_photo_too(api: Api, alice: str) -> None:
    body = upload(api, alice, images.png())
    saved = api.request(
        "PUT", "/api/profile/facts", token=alice, json={"base_revision": 0, "data": full("facts")}
    ).json()
    assert saved["photo_etag"] == body["etag"]


# --- caching ------------------------------------------------------------------------------------


def test_if_none_match_gives_304(api: Api, alice: str) -> None:
    etag = upload(api, alice, images.png())["etag"]
    for header in (etag, f"W/{etag}", f'"other", {etag}', "*"):
        response = get(api, alice, headers={"If-None-Match": header})
        assert response.status_code == 304, header
        assert response.content == b""
        assert response.headers["etag"] == etag
        assert response.headers["cache-control"] == PHOTO_CACHE


@pytest.mark.parametrize("header", ['"nope"', "", "W/", '"' + "0" * 64 + '"', "garbage"])
def test_if_none_match_that_does_not_match_gives_the_image(
    api: Api, alice: str, header: str
) -> None:
    upload(api, alice, images.png())
    response = get(api, alice, headers={"If-None-Match": header})
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")


def test_old_etag_after_a_new_upload_gives_the_new_image(api: Api, alice: str) -> None:
    old = upload(api, alice, images.png())["etag"]
    new = upload(api, alice, images.jpeg())["etag"]
    response = get(api, alice, headers={"If-None-Match": old})
    assert response.status_code == 200
    assert response.headers["etag"] == new


def test_get_without_a_photo_is_404(api: Api, alice: str) -> None:
    response = get(api, alice)
    assert response.status_code == 404
    assert response.json() == {"detail": NO_PHOTO}
    assert get(api, alice, headers={"If-None-Match": "*"}).status_code == 404


# --- delete -------------------------------------------------------------------------------------


def test_delete_is_idempotent(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    upload(api, alice, images.png())
    deleted_at = clock.advance(minutes=1)
    first = delete(api, alice)
    assert first.status_code == 204
    assert first.content == b""
    assert row(factory, ALICE) == (None, None)
    assert profile(api, alice)["updated_at"] == iso(deleted_at)
    clock.advance(minutes=1)
    second = delete(api, alice)
    assert second.status_code == 204
    # Nothing to remove: nothing changes, not even updated_at.
    assert profile(api, alice)["updated_at"] == iso(deleted_at)
    assert get(api, alice).status_code == 404


def test_delete_without_any_profile_row(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    assert delete(api, alice).status_code == 204
    assert row(factory, ALICE) is None


def test_upload_after_delete(api: Api, alice: str) -> None:
    upload(api, alice, images.png())
    delete(api, alice)
    body = upload(api, alice, images.jpeg())
    assert get(api, alice).headers["etag"] == body["etag"]


# --- size ---------------------------------------------------------------------------------------


def padded_jpeg(total: int) -> bytes:
    jpeg = images.jpeg(64, 48)
    return jpeg + b"\x00" * (total - len(jpeg))


def test_exactly_two_megabytes_is_accepted(api: Api, alice: str) -> None:
    body = upload(api, alice, padded_jpeg(PHOTO_MAX_BYTES))
    assert (body["width"], body["height"]) == (64, 48)
    assert body["size"] < 10_000


def test_one_byte_over_is_413(api: Api, alice: str, factory: sessionmaker[Session]) -> None:
    response = put(api, alice, padded_jpeg(PHOTO_MAX_BYTES + 1))
    assert response.status_code == 413
    assert response.json() == {"detail": "The image is larger than 2 MB."}
    assert row(factory, ALICE) is None


def test_streamed_body_over_the_limit_is_413(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    """Without a Content-Length (chunked), the body is cut as soon as it passes the limit."""
    sent = 0

    def chunks() -> Iterator[bytes]:
        nonlocal sent
        yield images.jpeg()
        for _ in range(64):
            sent += 64 * 1024
            yield b"\x00" * 64 * 1024

    response = put(api, alice, chunks())
    assert response.status_code == 413
    assert row(factory, ALICE) is None


def test_a_lying_content_length_is_refused(api: Api, alice: str) -> None:
    response = put(api, alice, images.png(), headers={"Content-Length": str(PHOTO_MAX_BYTES + 1)})
    assert response.status_code == 413


# --- refused files ------------------------------------------------------------------------------

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
PDF = b"%PDF-1.7\n%%EOF\n"
HTML = b"<!doctype html><script>alert(document.cookie)</script>"


@pytest.mark.parametrize(
    ("data", "detail"),
    [
        (b"", photos.EMPTY),
        (images.gif(), photos.ONLY_PNG_JPEG),
        (images.webp(), photos.ONLY_PNG_JPEG),
        (SVG, photos.ONLY_PNG_JPEG),
        (PDF, photos.ONLY_PNG_JPEG),
        (HTML, photos.ONLY_PNG_JPEG),
        (b'{"base_revision": 0, "data": {}}', photos.ONLY_PNG_JPEG),
        (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32, photos.ONLY_PNG_JPEG),
        (b"\xff\xd8\xff\xe0" + HTML, photos.UNREADABLE),
        (images.png(200, 200)[:-40], photos.UNREADABLE),
        (images.jpeg(200, 200)[:-300], photos.UNREADABLE),
    ],
    ids=[
        "empty", "gif", "webp", "svg", "pdf", "html", "json", "heic", "polyglot",
        "truncated-png", "truncated-jpeg",
    ],
)  # fmt: skip
def test_refused_files_are_415_and_nothing_is_stored(
    api: Api, alice: str, factory: sessionmaker[Session], data: bytes, detail: str
) -> None:
    upload(api, alice, images.png())
    before = row(factory, ALICE)
    response = put(api, alice, data)
    assert response.status_code == 415
    assert response.json() == {"detail": detail}
    assert row(factory, ALICE) == before


def test_decompression_bomb_is_422(api: Api, alice: str, factory: sessionmaker[Session]) -> None:
    response = put(api, alice, images.bomb_png(20000))
    assert response.status_code == 422
    assert response.json() == {"detail": photos.TOO_MANY_PIXELS}
    assert row(factory, ALICE) is None


def test_valid_jpeg_with_html_appended_is_stored_clean(api: Api, alice: str) -> None:
    upload(api, alice, images.jpeg() + HTML)
    content = get(api, alice).content
    assert b"<script>" not in content
    assert content.endswith(b"\xff\xd9")


# --- access -------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["PUT", "GET", "DELETE"])
def test_anonymous_is_401(api: Api, method: str) -> None:
    response = api.request(method, URL, content=images.png() if method == "PUT" else None)
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}


def test_anonymous_upload_is_refused_before_the_body_is_decoded(
    api: Api, factory: sessionmaker[Session]
) -> None:
    assert put(api, None, images.bomb_png(20000)).status_code == 401
    assert put(api, "not-a-session", images.png()).status_code == 401
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Profile)) == 0


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_csrf_header_is_required(
    api: Api, alice: str, factory: sessionmaker[Session], method: str
) -> None:
    upload(api, alice, images.png())
    before = row(factory, ALICE)
    response = api.request(
        method, URL, token=alice, csrf=False, content=images.jpeg() if method == "PUT" else None
    )
    assert response.status_code == 403
    assert response.json() == {"detail": CSRF_REFUSED}
    assert row(factory, ALICE) == before
    wrong = api.request(
        method, URL, token=alice, csrf=False, headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert wrong.status_code == 403


def test_get_needs_no_csrf_header(api: Api, alice: str) -> None:
    upload(api, alice, images.png())
    assert get(api, alice, csrf=False).status_code == 200


def test_post_and_patch_are_not_routes(api: Api, alice: str) -> None:
    assert api.post(URL, token=alice, content=images.png()).status_code == 405
    assert api.request("PATCH", URL, token=alice, content=images.png()).status_code == 405


# --- isolation ----------------------------------------------------------------------------------


def test_users_never_see_or_touch_each_others_photo(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    alice_photo = upload(api, alice, images.png())
    # Bob has none: 404, even when he presents Alice's ETag.
    assert get(api, bob).status_code == 404
    assert get(api, bob, headers={"If-None-Match": alice_photo["etag"]}).status_code == 404
    assert profile(api, bob)["has_photo"] is False
    # Bob's delete removes nothing of Alice's.
    assert delete(api, bob).status_code == 204
    assert get(api, alice).headers["etag"] == alice_photo["etag"]
    # Bob's upload is his own.
    bob_photo = upload(api, bob, images.jpeg())
    assert get(api, bob).headers["etag"] == bob_photo["etag"]
    assert get(api, alice).headers["etag"] == alice_photo["etag"]
    assert row(factory, ALICE)[1] == "image/png"  # type: ignore[index]
    assert row(factory, BOB)[1] == "image/jpeg"  # type: ignore[index]
    # Alice's delete leaves Bob's.
    delete(api, alice)
    assert get(api, alice).status_code == 404
    assert get(api, bob).headers["etag"] == bob_photo["etag"]


def test_service_reads_only_the_callers_photo(factory: sessionmaker[Session]) -> None:
    from datetime import UTC

    alice = make_user(factory, email=ALICE)
    bob = make_user(factory, email=BOB)
    photo = photos.normalize(images.png())
    with factory() as db:
        service.set_photo(db, alice.id, photo, datetime.now(UTC))
        db.commit()
    with factory() as db:
        assert service.get_photo(db, bob.id) is None
        assert service.photo_state(db, bob.id) is None
        assert service.delete_photo(db, bob.id, datetime.now(UTC)) is False
        db.commit()
        state = service.photo_state(db, alice.id)
        assert state is not None
        assert (state.mime, state.etag) == ("image/png", photo.etag)
        assert service.get_photo(db, alice.id) == (photo.data, "image/png")
        assert service.delete_photo(db, alice.id, datetime.now(UTC)) is True
        db.commit()
        assert service.get_photo(db, alice.id) is None


def test_photo_removed_between_the_etag_and_the_read_is_404(
    api: Api, alice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload(api, alice, images.png())
    monkeypatch.setattr(service, "get_photo", lambda db, user_id: None)
    response = get(api, alice)
    assert response.status_code == 404
    assert response.json() == {"detail": NO_PHOTO}
