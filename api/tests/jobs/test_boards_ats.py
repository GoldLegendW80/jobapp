"""Company boards: each ATS's answer as postings (hand-made fixtures in boards_fixtures/, no
network), a pasted board address as an ATS and an id, the contract words, the starter list."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.jobs.boards import ats, catalog
from app.jobs.models import ContractType
from app.profile.schemas import CompanyBoard, SearchSection

FIXTURES = Path(__file__).with_name("boards_fixtures")


def body(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def test_ashby_listed_postings_with_every_place_remote_and_contract() -> None:
    parsed = ats.parse("ashby", "example-robotics", body("ashby"))
    titles = [p.title for p in parsed.postings]
    assert "AI Engineer, unlisted" not in titles  # isListed false
    assert parsed.invalid == 2  # no address, not an object
    first = parsed.postings[0]
    assert first.location == "Paris, London"  # duplicates removed, in order
    assert (first.published, first.contract) == ("2026-10-01", ContractType.CDI)
    assert first.url == "https://jobs.ashbyhq.com/example-robotics/1111"
    intern = next(p for p in parsed.postings if p.title == "AI Engineer Intern")
    assert (intern.location, intern.contract) == ("Remote", ContractType.Stage)


def test_greenhouse_text_unescaped_twice_and_dates() -> None:
    parsed = ats.parse("greenhouse", "examplelabs", body("greenhouse"))
    assert parsed.invalid == 1
    first, remote = parsed.postings
    assert first.text == "Build agents & tools in Python."
    assert (first.location, first.published, first.contract) == ("London, UK", "2026-10-02", None)
    assert remote.published == "2026-10-06"  # no first_published: updated_at


def test_lever_places_commitment_and_date() -> None:
    parsed = ats.parse("lever", "exampleworks", body("lever"))
    assert parsed.invalid == 1
    fde, contract = parsed.postings
    assert (fde.location, fde.published, fde.contract) == ("Paris, Lyon", "2026-10-03", "CDI")
    # "Contract" alone is unknown (fixed term or freelance); remote is added to the places.
    assert (contract.location, contract.contract) == ("Berlin, Remote", None)


def test_lever_unknown_company_is_not_found() -> None:
    with pytest.raises(ats.BoardError) as error:
        ats.parse("lever", "nobody", b'{"ok": false, "error": "Document not found"}')
    assert error.value.code == "not_found"


def test_smartrecruiters_posting_address_and_no_text() -> None:
    parsed = ats.parse("smartrecruiters", "ExampleConsulting", body("smartrecruiters"))
    assert (parsed.total, parsed.invalid) == (2, 1)
    (one,) = parsed.postings
    assert one.url == "https://jobs.smartrecruiters.com/ExampleConsulting/700000000000001"
    assert (one.location, one.text, one.contract) == ("Paris, FR", "", ContractType.CDI)


def test_workable_hidden_places_left_out() -> None:
    (one,) = ats.parse("workable", "examplemodels", body("workable")).postings
    assert one.location == "Paris, France, Remote"
    assert (one.text, one.published) == ("Agents & evaluation in Python.", "2026-10-05")


def test_recruitee_text_and_internship() -> None:
    (one,) = ats.parse("recruitee", "examplelabs", body("recruitee")).postings
    assert (one.text, one.contract, one.published) == ("LLM agents. Python", "Stage", "2026-10-06")


@pytest.mark.parametrize("kind", sorted(ats.PARSERS))
@pytest.mark.parametrize("raw", [b"<html>", b"[1", b'"text"', b"{}"])
def test_a_body_of_the_wrong_shape_is_invalid(kind: str, raw: bytes) -> None:
    with pytest.raises(ats.BoardError) as error:
        ats.parse(kind, "example", raw)
    assert error.value.code == "invalid_response"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "/relative/1",
        "https://user:pw@example-robotics.test/1",
        "https://example-robotics.test:x/1",
        "https://example-robotics.test/a b",
        "https://example-robotics.test/" + "a" * 2000,
    ],
)
def test_a_posting_whose_address_is_not_a_link_is_invalid(url: str) -> None:
    raw = json.dumps({"jobs": [{"title": "AI Engineer", "jobUrl": url}]})
    parsed = ats.parse("ashby", "x", raw.encode())
    assert (parsed.postings, parsed.invalid) == ([], 1)


def test_long_fields_are_cut_to_what_a_job_takes() -> None:
    job = {"title": "A" * 500, "location": "P" * 500, "jobUrl": "https://x.test/1"}
    job["descriptionPlain"] = "t" * 300_000
    (one,) = ats.parse("ashby", "x", json.dumps({"jobs": [job]}).encode()).postings
    assert (len(one.title), len(one.location), len(one.text)) == (300, 300, 200_000)


def test_storable_text() -> None:
    raw = json.dumps({"jobs": [{"title": "AI\x00 Engineer", "jobUrl": "https://x.test/1"}]})
    (one,) = ats.parse("ashby", "x", raw.encode()).postings
    assert one.title == "AI Engineer"


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("https://jobs.ashbyhq.com/Example-Robotics", ("ashby", "example-robotics")),
        ("jobs.ashbyhq.com/mistral.ai/1234-abcd?utm=x", ("ashby", "mistral.ai")),
        ("https://api.ashbyhq.com/posting-api/job-board/openai", ("ashby", "openai")),
        ("https://boards.greenhouse.io/examplelabs", ("greenhouse", "examplelabs")),
        ("https://job-boards.greenhouse.io/examplelabs/jobs/42", ("greenhouse", "examplelabs")),
        (
            "https://boards.greenhouse.io/embed/job_board?for=examplelabs",
            ("greenhouse", "examplelabs"),
        ),
        ("https://boards-api.greenhouse.io/v1/boards/examplelabs/jobs", ("greenhouse", "examplelabs")),  # noqa: E501
        ("https://jobs.lever.co/exampleworks/abc", ("lever", "exampleworks")),
        ("https://jobs.smartrecruiters.com/Example/1", ("smartrecruiters", "Example")),
        ("https://careers.smartrecruiters.com/Example", ("smartrecruiters", "Example")),
        ("https://apply.workable.com/examplemodels/", ("workable", "examplemodels")),
        ("https://examplemodels.workable.com/", ("workable", "examplemodels")),
        ("https://examplelabs.recruitee.com/o/ai-engineer", ("recruitee", "examplelabs")),
    ],
)  # fmt: skip
def test_detect_a_pasted_board(address: str, expected: tuple[str, str]) -> None:
    found = ats.detect(address)
    assert found is not None
    assert (found.ats, found.board) == expected


@pytest.mark.parametrize(
    "address",
    [
        "https://jobs.ashbyhq.com/",
        "https://example.org/jobs.ashbyhq.com/openai",
        "https://jobs.ashbyhq.com.evil.test/openai",
        "https://user:pw@jobs.ashbyhq.com/openai",
        "https://jobs.ashbyhq.com:8443/openai",
        "ftp://jobs.ashbyhq.com/openai",
        "https://apply.workable.com/j/ABC123",
        "https://boards.greenhouse.io/embed/job_board",
        "https://jobs.ashbyhq.com/a..b",
        "https://job-boards.eu.greenhouse.io/examplelabs",
        "https://a.b.recruitee.com/",
        "not a url at all",
    ],
)
def test_detect_refuses_what_is_not_a_board(address: str) -> None:
    assert ats.detect(address) is None


def test_company_name_from_the_id() -> None:
    assert ats.Detected("ashby", "mistral.ai").company == "Mistral"
    assert ats.Detected("ashby", "example-robotics").company == "Example Robotics"


def test_api_urls_are_fixed_hosts_and_one_segment() -> None:
    assert ats.api_url("ashby", "mistral.ai") == (
        "https://api.ashbyhq.com/posting-api/job-board/mistral.ai?includeCompensation=true"
    )
    assert ats.api_url("smartrecruiters", "Example", 1).endswith("limit=100&offset=100")
    assert (
        ats.api_url("recruitee", "examplelabs") == "https://examplelabs.recruitee.com/api/offers/"
    )
    for bad in ("../x", "a/b", "a..b", "-a", "a?b"):
        with pytest.raises(ValueError, match="invalid board id"):
            ats.api_url("greenhouse", bad)
    with pytest.raises(ValueError, match="invalid board id"):
        ats.api_url("recruitee", "Upper")  # a DNS label in lower case only


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        ("FullTime", "CDI"), ("Full-time", "CDI"), ("fulltime_permanent", "CDI"),
        ("Intern", "Stage"), ("internship", "Stage"), ("International", None),
        ("Temporary", "CDD"), ("fulltime_fixed_term", "CDD"), ("freelance", "Freelance"),
        ("apprenticeship", "Alternance"), ("Contract", None), ("PartTime", None), (None, None),
    ],
)  # fmt: skip
def test_contract_words(words: str | None, expected: str | None) -> None:
    assert ats.contract_of(words) == expected


def test_the_starter_list() -> None:
    seed = catalog.seed()
    assert len(seed) == 33
    assert len({board.key for board in seed}) == 33
    assert {board.ats for board in seed} == {
        "ashby", "greenhouse", "lever", "smartrecruiters", "workable",
    }  # fmt: skip
    assert all(ats.valid_board(board.ats, board.board) for board in seed)
    # No security company (CLAUDE.md rule 2).
    assert "lakera.ai" not in {board.board for board in seed}


def test_enabled_boards_read_only_a_saved_list() -> None:
    def section(boards: object) -> SearchSection:
        data = {} if boards is None else {"company_boards": boards}
        return SearchSection.model_validate(data)

    assert catalog.enabled_boards(None) == []
    assert catalog.enabled_boards(section(None)) == []  # never saved: no request
    boards = [
        {"company": "Example Robotics", "ats": "ashby", "board": "example-robotics"},
        {"company": "Example Labs", "ats": "greenhouse", "board": "examplelabs", "enabled": False},
    ]
    assert [b.board for b in catalog.enabled_boards(section(boards))] == ["example-robotics"]


@pytest.mark.parametrize(
    "board",
    [
        {"company": "X", "ats": "ashby", "board": "a/b"},
        {"company": "X", "ats": "ashby", "board": "a..b"},
        {"company": "X", "ats": "recruitee", "board": "Example"},
        {"company": "X", "ats": "teamtailor", "board": "x"},
        {"company": "", "ats": "ashby", "board": "x"},
        {"company": "X", "ats": "ashby", "board": "x", "extra": 1},
    ],
)
def test_a_board_of_the_wrong_shape_is_refused(board: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CompanyBoard.model_validate(board)


def test_a_board_twice_is_refused() -> None:
    boards = [
        {"company": "A", "ats": "ashby", "board": "Example"},
        {"company": "B", "ats": "ashby", "board": "example"},
    ]
    with pytest.raises(ValidationError, match="duplicate company job board"):
        SearchSection.model_validate({"company_boards": boards})
