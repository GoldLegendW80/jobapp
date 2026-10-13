"""Helpers of the ingestion tests: made up files of every kind, calls of the documents routes,
rows read straight from the database, and a driver that feeds the app a body message by message
(what a test client that sends a body whole cannot do)."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.identity.deps import COOKIE_NAME
from app.identity.models import User
from app.ingestion.models import IngestionDocument, IngestionImage
from tests.identity.api_helpers import Api

URL = "/api/ingestion/documents"
MIB = 1024 * 1024

# A fictitious person: nothing here belongs to anyone.
DOSSIER = (
    "# Robin Example\n\n"
    "Applied AI engineer in Nantes, robin@example.org.\n\n"
    "## Experience\n\n"
    "- Acme Robotics, AI engineer, since March 2024.\n"
    "- Globex, developer, 2021 to 2024.\n"
)

PDF_MAGIC = b"%PDF-1.7\n"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff\xe0"
ZIP_MAGIC = b"PK\x03\x04"


def pdf(size: int = 2048, fill: bytes = b"\x00") -> bytes:
    """Bytes that start as a PDF does. Nothing parses them in G1: only the first bytes count."""
    return PDF_MAGIC + fill * (size - len(PDF_MAGIC))


def png(size: int = 256) -> bytes:
    return PNG_MAGIC + b"\x01" * (size - len(PNG_MAGIC))


def jpeg(size: int = 256) -> bytes:
    return JPEG_MAGIC + b"\x02" * (size - len(JPEG_MAGIC))


def archive(first_name: bytes = b"[Content_Types].xml", content: bytes = b"<Types/>") -> bytes:
    """The start of a ZIP: one local file header, the entry's name, its content."""
    header = ZIP_MAGIC + b"\x14\x00\x00\x00\x00\x00" + b"\x00" * 8
    sizes = len(content).to_bytes(4, "little") * 2
    lengths = len(first_name).to_bytes(2, "little") + b"\x00\x00"
    return header + sizes + lengths + first_name + content


def secret_like(prefix: str = "AKIA") -> str:
    """A value with the shape of an AWS access key, built here so no scanner of this repository
    takes the source for a leak. It opens nothing."""
    return prefix + "Z7XQ2M4K" + "T6WB3N5P"


def named(name: str) -> dict[str, str]:
    return {"X-Filename": "UTF-8''" + quote(name, safe="")}


def upload(
    api: Api,
    token: str | None,
    data: Any,
    name: str | None = "notes.md",
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    sent = {**(named(name) if name is not None else {}), **(headers or {})}
    return api.post(URL, token=token, content=data, headers=sent, **kwargs)


def uploaded(api: Api, token: str, data: bytes, name: str = "notes.md") -> dict[str, Any]:
    response = upload(api, token, data, name)
    assert response.status_code == 201, response.text
    return response.json()


def paste(api: Api, token: str | None, text: str, name: str | None = None) -> httpx.Response:
    body: dict[str, Any] = {"text": text}
    if name is not None:
        body["name"] = name
    return api.post(f"{URL}/text", token=token, json=body)


def listing(api: Api, token: str) -> dict[str, Any]:
    response = api.get(URL, token=token)
    assert response.status_code == 200, response.text
    return response.json()


def text_of(api: Api, token: str | None, document_id: str, **params: Any) -> httpx.Response:
    return api.get(f"{URL}/{document_id}/text", token=token, params=params)


def confirm(api: Api, token: str | None, document_id: str, **kwargs: Any) -> httpx.Response:
    return api.post(f"{URL}/{document_id}/confirm", token=token, **kwargs)


def delete(api: Api, token: str | None, document_id: str, **kwargs: Any) -> httpx.Response:
    return api.request("DELETE", f"{URL}/{document_id}", token=token, **kwargs)


def rows(factory: sessionmaker[Session]) -> list[IngestionDocument]:
    """Every document of every user, oldest first, with its bytes and text loaded."""
    with factory() as db:
        return list(
            db.execute(
                sa.select(IngestionDocument)
                .options(undefer(IngestionDocument.raw), undefer(IngestionDocument.text))
                .order_by(IngestionDocument.created_at, IngestionDocument.id)
            ).scalars()
        )


def row(factory: sessionmaker[Session], document_id: str) -> IngestionDocument:
    found = [doc for doc in rows(factory) if str(doc.id) == document_id]
    assert len(found) == 1, f"{len(found)} rows for {document_id}"
    return found[0]


def user_id(factory: sessionmaker[Session], email: str) -> uuid.UUID:
    with factory() as db:
        return db.execute(sa.select(User.id).where(User.email == email)).scalar_one()


def set_columns(factory: sessionmaker[Session], document_id: str, **values: Any) -> None:
    """Put a row in a state only a later task's parser produces (failed, done with a note)."""
    with factory() as db, db.begin():
        db.execute(
            sa.update(IngestionDocument)
            .where(IngestionDocument.id == uuid.UUID(document_id))
            .values(**values)
        )


def add_image(factory: sessionmaker[Session], document_id: str, position: int = 0) -> None:
    with factory() as db, db.begin():
        owner = db.execute(
            sa.select(IngestionDocument.user_id).where(
                IngestionDocument.id == uuid.UUID(document_id)
            )
        ).scalar_one()
        db.add(
            IngestionImage(
                document_id=uuid.UUID(document_id),
                user_id=owner,
                position=position,
                jpeg=jpeg(1000),
                width=800,
                height=600,
            )
        )


def count(factory: sessionmaker[Session], model: type) -> int:
    with factory() as db:
        return db.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()


# --- a body fed message by message ---------------------------------------------------------


@dataclass
class Answer:
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    # http.request messages the app took before it answered
    taken: int = 0

    def json(self) -> Any:
        return json.loads(self.body)


Messages = Callable[[], AsyncIterator[dict[str, Any]]]


async def chunks(*parts: bytes, pause: float = 0.0) -> AsyncIterator[dict[str, Any]]:
    """Body messages for `parts`, the last one closing the body, `pause` seconds apart."""
    for index, part in enumerate(parts):
        if pause:
            await asyncio.sleep(pause)
        yield {"type": "http.request", "body": part, "more_body": index < len(parts) - 1}


async def post_stream(
    app: FastAPI,
    token: str,
    messages: Messages,
    headers: dict[str, str] | None = None,
    path: str = URL,
) -> Answer:
    """POST to the app over ASGI itself: the body arrives as `messages` yields it, and once they
    are exhausted the client stays silent (a stalled upload) unless a message said otherwise."""
    sent = {
        "host": "testserver",
        "cookie": f"{COOKIE_NAME}={token}",
        "x-requested-with": "jobapp",
        **{key.lower(): value for key, value in (headers or {}).items()},
    }
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(key.encode(), value.encode("latin-1")) for key, value in sent.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    answer = Answer()
    source = messages()

    async def receive() -> dict[str, Any]:
        try:
            message = await anext(source)
        except StopAsyncIteration:
            await asyncio.Event().wait()  # silence
            raise AssertionError("unreachable") from None
        if message["type"] == "http.request":
            answer.taken += 1
        return message

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            answer.status = message["status"]
            answer.headers = {
                key.decode().lower(): value.decode() for key, value in message["headers"]
            }
        elif message["type"] == "http.response.body":
            answer.body += message.get("body", b"")

    await asyncio.wait_for(app(scope, receive, send), timeout=60)
    return answer


def stream_post(
    app: FastAPI,
    token: str,
    messages: Messages,
    headers: dict[str, str] | None = None,
    path: str = URL,
) -> Answer:
    """`post_stream` on a loop of its own."""
    return asyncio.run(post_stream(app, token, messages, headers, path))
