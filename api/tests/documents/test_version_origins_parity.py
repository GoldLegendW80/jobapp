"""The shared fixture (fixtures/version_origins.json) lists the origins a document version can
carry; web/src/features/documents/test/origins.test.ts checks the web's schema and labels against
the same file. This side proves the file still says what `Origin` holds, so a new origin fails one
of the two tests instead of the documents page, which refuses a whole answer with an unknown one."""

import json
from pathlib import Path

from app.documents.models import Origin

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "version_origins.json").read_text(encoding="utf-8")
)


def test_the_shared_list_is_the_api_origin_enum() -> None:
    assert FIXTURE["origins"] == [origin.value for origin in Origin]
