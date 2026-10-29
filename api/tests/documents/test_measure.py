"""The measure tool of the document writers (app/documents/measure.py): the table and the JSON
from scripted outputs, the refusal without a credential, the credential nowhere, and only fixture
data read. The model is the recorded streams of app/agents/fake_streams/ (no model call, no
network); Postgres is real (the identity fixtures)."""

from __future__ import annotations

import builtins
import io
import json
import logging
import os
import secrets
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
import sqlalchemy as sa
from claude_agent_sdk import AssistantMessage, TextBlock
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.agents.crypto import Keyring
from app.agents.fake_backend import FakeBackend
from app.agents.models import CredentialKind
from app.documents import measure
from app.documents.measure import Credential, Env, MeasureError, Pair, Runtime, Sample
from tests.agents.conftest import API_KEY, OAUTH, OTHER_KEY, new_key
from tests.agents.run_helpers import Backends
from tests.conftest import make_settings
from tests.documents import canary
from tests.documents import store_support as support

AT = support.AT
engine, factory, migrated_url = support.engine, support.factory, support.migrated_url  # fixtures
REPO = Path(__file__).resolve().parents[3]
ALEX = Pair("alex", "lumen")
API = Credential(CredentialKind.anthropic_api_key, API_KEY, "ANTHROPIC_API_KEY")
# Recorded streams (app/agents/fake_streams/documents_cv_<name>.json), one per repetition.
CV_SCRIPTS = {0: "valid", 1: "number_fixed", 2: "rewrite_failed"}


@pytest.fixture
def recorder() -> Iterator[measure.TakeRecorder]:
    with measure.TakeRecorder() as taken:
        yield taken


@pytest.fixture
def make(
    factory: sessionmaker[Session],
    migrated_url: str,
    recorder: measure.TakeRecorder,
) -> Callable[..., Env]:
    def build(
        scripts: dict[tuple[str, int], str] | None = None,
        *,
        credential: Credential = API,
        backend: Callable[[], Any] | None = None,
        fixtures: measure.Fixtures | None = None,
    ) -> Env:
        key = new_key()
        settings = make_settings(migrated_url, credentials_key=key, agents_fake_backend=True)
        found = scripts or {}
        runtime = Runtime(
            backend or Backends(speed=0.0),
            fake=True,
            script_for=lambda writer, pair, repetition: (
                f"documents_{writer}_{found[(writer, repetition)]}"
                if (writer, repetition) in found
                else None
            ),
        )
        return Env(
            factory,
            settings,
            Keyring.from_text(key),
            credential,
            fixtures or measure.Fixtures(),
            runtime,
            lambda: AT,
            recorder,
            lambda line: None,
        )

    return build


def cv_scripts() -> dict[tuple[str, int], str]:
    return {("cv", rep): name for rep, name in CV_SCRIPTS.items()}


# --- the credential: refused without, never shown -----------------------------------------------


def run_main(
    argv: list[str], env: dict[str, str], tmp_path: Path, **kwargs: Any
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = measure.main(
        ["--json", str(tmp_path / "out.json"), *argv], env=env, out=out, err=err, **kwargs
    )
    return code, out.getvalue(), err.getvalue()


def never(*_: Any) -> Any:
    raise AssertionError("nothing may start before the command is accepted")


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"CLAUDE_CODE_OAUTH_TOKEN": "", "ANTHROPIC_API_KEY": ""},
        {"CLAUDE_CODE_OAUTH_TOKEN": "  \n", "ANTHROPIC_API_KEY": "\t"},
        {"MEASURE_DATABASE_URL": "postgresql://u:p@127.0.0.1:1/x"},
        {"CLAUDE_API_KEY": API_KEY, "ANTHROPIC_AUTH_TOKEN": OAUTH},  # other names are not read
    ],
)
def test_it_refuses_to_start_without_a_credential(env: dict[str, str], tmp_path: Path) -> None:
    code, out, err = run_main([], env, tmp_path, runtime=Runtime(never))
    assert code == 2
    assert out == ""
    assert err.count("\n") == 1  # one sentence
    assert "CLAUDE_CODE_OAUTH_TOKEN" in err
    assert "ANTHROPIC_API_KEY" in err
    assert not (tmp_path / "out.json").exists()


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("CLAUDE_CODE_OAUTH_TOKEN", API_KEY),  # a key under the subscription name
        ("ANTHROPIC_API_KEY", OAUTH),  # a subscription token under the key name
        ("ANTHROPIC_API_KEY", "sk-ant-admin01-" + "Ab1_" * 12),
        ("ANTHROPIC_API_KEY", "not-a-key"),
        ("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-short"),
        ("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "a b " * 12),
        ("ANTHROPIC_API_KEY", API_KEY[:-1] + "!"),
    ],
)
def test_a_credential_of_the_wrong_shape_is_refused_by_name_never_quoted(
    variable: str, value: str, tmp_path: Path
) -> None:
    code, out, err = run_main([], {variable: value}, tmp_path, runtime=Runtime(never))
    assert code == 2
    assert variable in err
    assert err.count("\n") == 1
    assert value not in out + err
    assert value[:20] not in out + err


def test_the_subscription_token_wins_when_both_are_set_and_only_the_kind_is_named() -> None:
    both = measure.credential_from({"CLAUDE_CODE_OAUTH_TOKEN": OAUTH, "ANTHROPIC_API_KEY": API_KEY})
    assert both.kind is CredentialKind.claude_subscription
    assert both.variable == "CLAUDE_CODE_OAUTH_TOKEN"
    only = measure.credential_from({"ANTHROPIC_API_KEY": f"  {API_KEY}\n"})
    assert only.kind is CredentialKind.anthropic_api_key
    assert OAUTH not in repr(both)
    assert API_KEY not in repr(both) + repr(only)


@pytest.mark.parametrize(
    ("argv", "env", "word"),
    [
        (["--runs", "0"], {}, "--runs"),
        (["--runs", "11"], {}, "--runs"),
        (["--max-pairs", "0"], {}, "--max-pairs"),
        (["--pairs", "alex:nowhere"], {}, "alex:nowhere"),
        (["--pairs", "dana:lumen"], {}, "dana:lumen"),  # a profile with no base CV
        (["--pairs", ""], {}, "(empty)"),
        ([], {}, "MEASURE_DATABASE_URL"),  # no database
        (["--database-url", "postgresql://u:p@db.example.org/x"], {}, "this machine"),
        (["--database-url", "postgresql://u:p@10.0.0.5/x"], {}, "this machine"),
        ([], {"TEST_DATABASE_URL": "postgresql://u:p@db/x"}, "this machine"),
        (["--database-url", "not a url"], {}, "valid"),
        (["--json", str(REPO / "measure.json")], {}, "outside this repository"),
        (["--json", str(REPO / "api" / ".." / "x" / "m.json")], {}, "outside this repository"),
    ],
)
def test_a_request_that_cannot_run_is_refused_before_anything_starts(
    argv: list[str], env: dict[str, str], word: str
) -> None:
    out, err = io.StringIO(), io.StringIO()
    code = measure.main(
        argv,
        env={"CLAUDE_CODE_OAUTH_TOKEN": OAUTH, **env},
        out=out,
        err=err,
        runtime=Runtime(never),
    )
    assert code == 2
    assert word in err.getvalue()
    assert out.getvalue() == ""
    assert OAUTH not in err.getvalue()


def test_the_database_comes_from_the_measure_variable_first_and_never_from_the_app() -> None:
    chosen = measure.database_server(
        None,
        {
            "MEASURE_DATABASE_URL": "postgresql://u:p@localhost/a",
            "TEST_DATABASE_URL": "postgresql://u:p@127.0.0.1/b",
            "DATABASE_URL": "postgresql://u:p@127.0.0.1/production",
        },
    )
    assert chosen.endswith("/a")
    with pytest.raises(MeasureError):
        measure.database_server(None, {"DATABASE_URL": "postgresql://u:p@127.0.0.1/production"})


# --- the pairs ----------------------------------------------------------------------------------


def test_the_default_pairs_are_five_different_people_for_five_different_postings() -> None:
    fixtures = measure.Fixtures()
    pairs = measure.chosen_pairs(fixtures, None, 5)
    assert [p.name for p in pairs] == [
        "alex:brise",
        "blake:brume",
        "casey:halo",
        "robin:lumen",
        "sacha:polaris",
    ]
    every = measure.all_pairs(fixtures)
    assert len(every) == len({p.name for p in every}) == 25
    # Only profiles that have a base CV: dana and the export of morgan have none.
    assert {p.profile for p in every} == {"alex", "blake", "casey", "robin", "sacha"}
    assert len(measure.chosen_pairs(fixtures, None, 3)) == 3
    assert len(measure.chosen_pairs(fixtures, None, 99)) == 25


def test_pairs_named_by_the_person_keep_their_order_and_drop_repeats() -> None:
    pairs = measure.chosen_pairs(measure.Fixtures(), " casey:brise, alex:lumen,casey:brise", 5)
    assert [p.name for p in pairs] == ["casey:brise", "alex:lumen"]


def test_only_json_files_named_by_a_plain_name_are_read(tmp_path: Path) -> None:
    (tmp_path / "profile_x.json").write_text('{"revision": 1, "sections": {}}')
    (tmp_path / "secret.json").write_text('{"token": "planted"}')
    (tmp_path / ".env").write_text("TOKEN=planted")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "profile_y.json").write_text("{}")
    fixtures = measure.Fixtures(tmp_path)
    assert fixtures.parts("profile") == ["x"]
    assert fixtures.json("profile_x.json")["revision"] == 1
    for name in (
        ".env",
        "../profile_x.json",
        "sub/profile_y.json",
        "/etc/passwd",
        "profile_x.json\n",
        "profile_x.JSON",
        "profile x.json",
        "profile_x.json.bak",
        "",
        "missing.json",
    ):
        with pytest.raises(MeasureError):
            fixtures.json(name)
    assert fixtures.read == ["profile_x.json"]


# --- the numbers and the table, from scripted samples -------------------------------------------


def sample(pair: str, writer: str, rep: int, status: str = "done", **figures: float) -> Sample:
    return Sample(
        pair, writer, rep, status, error=None if status == "done" else "run_failed", figures=figures
    )


def test_the_figures_of_a_group_are_mean_min_max_and_spread_of_the_runs_that_finished() -> None:
    samples = [
        sample("a:b", "cv", 0, tokens=1000, cost_usd=0.1, final_claims=0),
        sample("a:b", "cv", 1, tokens=1500, cost_usd=0.25, final_claims=2),
        sample("a:b", "cv", 2, tokens=2500, final_claims=1),
        sample("a:b", "cv", 3, status="failed"),
    ]
    [group] = measure.summarize(samples)
    assert (group.runs, group.done, group.errors) == (4, 3, {"run_failed": 1})
    tokens, cost = group.stats["tokens"], group.stats["cost_usd"]
    assert (tokens.n, tokens.min, tokens.max, tokens.spread) == (3, 1000, 2500, 1500)
    assert tokens.mean == pytest.approx(5000 / 3)
    assert (cost.n, cost.mean, cost.min, cost.max) == (2, pytest.approx(0.175), 0.1, 0.25)
    assert cost.spread == pytest.approx(0.15)
    assert group.stats["final_claims"].spread == 2
    assert "rounds" not in group.stats  # a figure nobody has is not shown as zero


def test_the_table_has_a_block_per_pair_and_writer_then_one_over_all_pairs() -> None:
    samples = [
        sample("a:b", "cv", 0, tokens=1000, cost_usd=0.1, rounds=2),
        sample("a:b", "cv", 1, tokens=2500, cost_usd=0.25, rounds=3),
        sample("a:b", "letter", 0, tokens=700, rounds=1),
        sample("c:d", "cv", 0, status="failed"),
    ]
    text = measure.table(measure.summarize(samples))
    assert text == (
        "a:b  cv  (2 runs, 2 finished)\n"
        "  figure                    mean         min         max      spread\n"
        "  rounds                     2.5           2           3           1\n"
        "  tokens                    1750        1000        2500        1500\n"
        "  cost_usd                 0.175         0.1        0.25        0.15\n"
        "\n"
        "a:b  letter  (1 runs, 1 finished)\n"
        "  figure                    mean         min         max      spread\n"
        "  rounds                       1           1           1           0\n"
        "  tokens                     700         700         700           0\n"
        "\n"
        "c:d  cv  (1 runs, 0 finished)\n"
        "  not finished: run_failed x1\n"
        "\n"
        "all pairs  cv  (3 runs, 2 finished)\n"
        "  figure                    mean         min         max      spread\n"
        "  rounds                     2.5           2           3           1\n"
        "  tokens                    1750        1000        2500        1500\n"
        "  cost_usd                 0.175         0.1        0.25        0.15\n"
        "  not finished: run_failed x1\n"
    )


def test_one_pair_gets_no_all_pairs_block() -> None:
    groups = measure.summarize([sample("a:b", "cv", 0, tokens=1), sample("a:b", "cv", 1, tokens=2)])
    assert [g.pair for g in groups] == ["a:b"]


def test_the_json_holds_everything_and_names_the_credential_by_kind_only() -> None:
    samples = [sample("a:b", "cv", 0, tokens=1000, rounds=2)]
    samples[0].first = {"headline": "First"}
    body = measure.report({"runs": 1}, API, samples, AT)
    text = json.dumps(body)
    assert body["credential"] == {"kind": "anthropic_api_key", "variable": "ANTHROPIC_API_KEY"}
    assert body["samples"][0]["first"] == {"headline": "First"}
    assert body["summary"][0]["stats"]["tokens"]["spread"] == 0
    assert body["started"] == AT.isoformat()
    assert API_KEY not in text
    assert API_KEY[-4:] not in text.replace("ANTHROPIC_API_KEY", "")


# --- a run, scored ------------------------------------------------------------------------------


def test_each_repetition_is_a_fresh_person_whose_output_is_scored_by_the_checker(
    make: Callable[..., Env],
    factory: sessionmaker[Session],
) -> None:
    env = make(cv_scripts())
    samples = [s for rep in range(3) for s in measure.run_pair(env, ALEX, "cv", rep)]
    assert [(s.writer, s.status) for s in samples] == [("cv", "done")] * 3
    clean, fixed, kept = samples
    # valid: one submit, nothing for the checker to find.
    assert clean.figures == {
        "rounds": 1, "turns": 3, "tool_calls": 7, "tokens": 3300, "cost_usd": 0.05, "seconds": 0,
        "first_claims": 0, "first_separators": 0, "first_notes": 0,
        "final_claims": 0, "final_separators": 0, "final_notes": 0,
        "fixed_by_writer": 0, "fixed_by_code": 0,
    }  # fmt: skip
    # number_fixed: an invented percentage in the first submit, fixed by the writer when asked.
    assert fixed.figures["rounds"] == 2
    assert (fixed.figures["first_claims"], fixed.figures["final_claims"]) == (1, 0)
    assert (fixed.figures["fixed_by_writer"], fixed.figures["fixed_by_code"]) == (1, 0)
    assert fixed.first_codes == {"percent_not_in_sources": 1}
    assert fixed.final_codes == {}
    # rewrite_failed: a claim the writer could not fix and the code kept for the person to see.
    assert (kept.figures["first_claims"], kept.figures["final_claims"]) == (1, 1)
    assert kept.final_codes == {"duration_not_in_profile": 1}
    assert kept.first is not None
    assert kept.final is not None
    [group, *rest] = measure.summarize(samples)
    assert rest == []
    claims = group.stats["final_claims"]
    assert (claims.mean, claims.min, claims.max, claims.spread) == (pytest.approx(1 / 3), 0, 1, 1)
    tokens = group.stats["tokens"]
    assert (tokens.mean, tokens.min, tokens.max, tokens.spread) == (
        pytest.approx(12100 / 3),
        3300,
        4400,
        1100,
    )
    assert group.stats["rounds"].spread == 1
    assert group.stats["cost_usd"].spread == 0
    with factory() as session:
        emails = session.scalars(sa.text("SELECT email FROM users ORDER BY email")).all()
    assert len(emails) == len(set(emails)) == 3  # a person per repetition
    assert all(e.startswith("e2e-measure-") and e.endswith("@example.org") for e in emails)


def test_a_run_that_fails_is_counted_with_its_code_and_adds_no_score(
    make: Callable[..., Env],
) -> None:
    env = make({("cv", 0): "bad_submits", ("cv", 1): "journey", ("cv", 2): "number_fixed"})
    samples = [s for rep in range(3) for s in measure.run_pair(env, ALEX, "cv", rep)]
    assert [s.status for s in samples] == ["failed", "failed", "done"]
    assert [s.error for s in samples] == ["invalid_output", "no_result", None]
    assert samples[0].figures["rounds"] == 4  # four submits, none valid
    assert "first_claims" not in samples[0].figures
    [group] = measure.summarize(samples)
    assert group.errors == {"invalid_output": 1, "no_result": 1}
    assert (group.runs, group.done, group.stats["rounds"].n) == (3, 1, 1)


def test_both_writes_the_letter_from_the_cv_with_what_the_cv_left_of_the_ceiling(
    make: Callable[..., Env],
    factory: sessionmaker[Session],
) -> None:
    env = make({("cv", 0): "number_fixed", ("letter", 0): "claims_only"})
    cv, letter = measure.run_pair(env, ALEX, "both", 0)
    assert (cv.writer, cv.status, letter.writer, letter.status) == ("cv", "done", "letter", "done")
    assert letter.figures["first_claims"] == 2
    assert letter.first_codes == {"employer_not_in_profile": 2}
    with factory() as session:
        ceilings = dict(
            session.execute(
                sa.text(
                    "SELECT d.kind::text, g.token_ceiling FROM document_generations g "
                    "JOIN documents d ON d.id = g.document_id"
                )
            ).all()
        )
    assert ceilings["letter"] == ceilings["cv"] - 4400  # the CV's run spent 4400 tokens


def test_a_letter_is_not_written_when_its_cv_failed(make: Callable[..., Env]) -> None:
    env = make({("cv", 0): "bad_submits"})
    cv, letter = measure.run_pair(env, ALEX, "both", 0)
    assert (cv.status, letter.status, letter.error) == ("failed", "failed", "cv_failed")


def test_a_letter_alone_is_written_without_a_cv(make: Callable[..., Env]) -> None:
    [letter] = measure.run_pair(make({("letter", 0): "claims_only"}), ALEX, "letter", 0)
    assert (letter.writer, letter.status) == ("letter", "done")


# --- only fixture data, no network --------------------------------------------------------------


def test_only_fixture_data_is_read_and_written(
    make: Callable[..., Env],
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    opened: list[str] = []
    real_open = io.open

    def watching(file: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, str | os.PathLike):
            opened.append(str(Path(file).resolve()))
        return real_open(file, *args, **kwargs)

    planted = tmp_path / "elsewhere.txt"
    planted.write_text("not a fixture")
    monkeypatch.setattr(builtins, "open", watching)
    monkeypatch.setattr(io, "open", watching)
    env = make(cv_scripts())
    planted.read_text()  # the control: this watcher does see a read
    measure.run_pair(env, ALEX, "cv", 0)
    app = Path(measure.API_DIR) / "app"
    # The recorded streams and the writers' prompts (the estimate reads their sizes).
    allowed = (
        measure.FIXTURES.resolve(),
        (app / "agents" / "fake_streams").resolve(),
        (app / "documents" / "prompts").resolve(),
    )
    outside = [p for p in opened if not any(Path(p).is_relative_to(root) for root in allowed)]
    assert outside == [str(planted.resolve())]
    assert env.fixtures.read == ["posting_lumen.json", "profile_alex.json", "base_cv_alex.json"]
    with factory() as session:
        users = session.execute(sa.text("SELECT id, email FROM users")).all()
        assert [u.email.startswith("e2e-measure-") for u in users] == [True]
        assert session.scalar(sa.text("SELECT count(*) FROM jobs")) == 1
        posting = json.loads((measure.FIXTURES / "posting_lumen.json").read_text())
        job = session.execute(sa.text("SELECT company, title, posting_text FROM jobs")).one()
        assert tuple(job) == (posting["company"], posting["title"], posting["text"])
        sections = session.scalar(sa.text("SELECT sections FROM profiles"))
        assert (
            sections == json.loads((measure.FIXTURES / "profile_alex.json").read_text())["sections"]
        )


def test_the_fixtures_hold_only_invented_people() -> None:
    fixtures = measure.Fixtures()
    texts = [fixtures.json(f"profile_{name}.json") for name in fixtures.parts("profile")]
    emails = {
        part.lower()
        for text in texts
        for part in json.dumps(text).replace('"', " ").split()
        if "@" in part
    }
    assert all(e.rstrip(".,").endswith(("@example.org", "@example.com")) for e in emails)


def test_a_run_on_the_fake_model_opens_no_socket_and_looks_up_no_name(
    make: Callable[..., Env], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The database is reached by libpq, in C; nothing else of the run touches the network."""
    reached: list[Any] = []
    real_connect, real_lookup = socket.socket.connect, socket.getaddrinfo

    def connecting(self: socket.socket, address: Any) -> Any:
        reached.append(address)
        return real_connect(self, address)

    def looking(host: Any, *args: Any, **kwargs: Any) -> Any:
        reached.append(host)
        return real_lookup(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connecting)
    monkeypatch.setattr(socket, "getaddrinfo", looking)
    env = make(cv_scripts())
    with (
        pytest.raises(ConnectionRefusedError),
        socket.socket() as probe,
    ):  # the control: a connect is seen
        probe.connect(("127.0.0.1", 1))
    assert reached == [("127.0.0.1", 1)]
    reached.clear()
    measure.run_pair(env, ALEX, "cv", 0)
    assert reached == []


# --- the command, end to end --------------------------------------------------------------------


def database_names(server: str) -> list[str]:
    admin = make_url(server).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        return [row[0] for row in conn.execute("SELECT datname FROM pg_database").fetchall()]


def throwaway_names(server: str) -> list[str]:
    admin = make_url(server).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE %s",
            (measure.DATABASE_PREFIX.replace("_", "\\_") + "%",),
        )
        return [row[0] for row in rows.fetchall()]


@pytest.fixture
def server(database_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The test server, with a prefix of its own for the databases of this test: another xdist
    worker may have its `measure_` database up at the same time."""
    monkeypatch.setattr(measure, "DATABASE_PREFIX", f"measure_{secrets.token_hex(4)}_")
    assert throwaway_names(database_url) == []
    return database_url


def test_main_prints_the_table_writes_the_json_and_drops_its_database(
    server: str, tmp_path: Path
) -> None:
    runtime = Runtime(
        Backends(speed=0.0),
        fake=True,
        script_for=lambda writer, pair, repetition: f"documents_cv_{CV_SCRIPTS[repetition]}",
    )
    path = tmp_path / "deep" / "result.json"
    out, err = io.StringIO(), io.StringIO()
    before = {
        name: os.environ.get(name) for name in ("DATABASE_URL", "APP_ENV", "AGENTS_FAKE_BACKEND")
    }
    take = measure.submit.take
    code = measure.main(
        ["--writer", "cv", "--pairs", "alex:lumen", "--runs", "3", "--json", str(path)],
        env={"CLAUDE_CODE_OAUTH_TOKEN": OAUTH, "TEST_DATABASE_URL": server},
        runtime=runtime,
        clock=lambda: AT,
        out=out,
        err=err,
    )
    assert code == 0, err.getvalue()
    assert "alex:lumen  cv  (3 runs, 3 finished)" in out.getvalue()
    row = f"  {'final_claims':<18}" + "".join(f"{cell:>12}" for cell in ("0.33", "0", "1", "1"))
    assert row + "\n" in out.getvalue()
    assert "all pairs" not in out.getvalue()  # one pair
    body = json.loads(path.read_text())
    assert body["args"] == {"runs": 3, "writer": "cv", "pairs": ["alex:lumen"]}
    assert body["credential"] == {
        "kind": "claude_subscription",
        "variable": "CLAUDE_CODE_OAUTH_TOKEN",
    }
    assert [s["status"] for s in body["samples"]] == ["done"] * 3
    assert body["samples"][0]["first"]["headline"]  # the texts are kept
    assert body["summary"][0]["stats"]["first_claims"]["mean"] == pytest.approx(2 / 3)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert throwaway_names(server) == []
    assert make_url(server).database in database_names(server)  # the server's own is still there
    # What the run changed in the process is back: the environment and the submit hook.
    assert {name: os.environ.get(name) for name in before} == before
    assert measure.submit.take is take
    assert "Credential: CLAUDE_CODE_OAUTH_TOKEN (claude_subscription)" in err.getvalue()


def test_the_database_is_dropped_when_the_run_crashes(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(*_: Any) -> Any:
        raise RuntimeError(f"boom {OAUTH}")

    monkeypatch.setattr(measure, "_run_all", crash)
    code, out, err = run_main(
        ["--pairs", "alex:lumen"],
        {"CLAUDE_CODE_OAUTH_TOKEN": OAUTH, "TEST_DATABASE_URL": server},
        tmp_path,
        runtime=Runtime(Backends(speed=0.0), fake=True),
    )
    assert code == 1
    assert "RuntimeError" in err  # the type and the place, no message
    assert OAUTH not in out + err
    assert throwaway_names(server) == []


# --- the credential appears nowhere -------------------------------------------------------------


class Hostile(FakeBackend):
    """A model that says the credential aloud in its text and fails with it in the message."""

    async def stream(self, request: Any) -> Any:
        secret = request.options.env["CLAUDE_CODE_OAUTH_TOKEN"]
        yield AssistantMessage(content=[TextBlock(text=f"my token is {secret}")], model="m")
        raise RuntimeError(f"upstream refused {secret}")


def test_the_credential_is_in_no_output_log_file_or_row(
    server: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "sk-ant-oat01-" + secrets.token_urlsafe(48)
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []
    real_dump = measure.report

    def keeping(*args: Any, **kwargs: Any) -> Any:
        seen.append(json.dumps(real_dump(*args, **kwargs)))
        return real_dump(*args, **kwargs)

    path = tmp_path / "result.json"
    out, err = io.StringIO(), io.StringIO()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(measure, "report", keeping)
        code = measure.main(
            ["--writer", "cv", "--pairs", "alex:lumen", "--runs", "2", "--json", str(path)],
            env={"CLAUDE_CODE_OAUTH_TOKEN": secret, "TEST_DATABASE_URL": server},
            runtime=Runtime(lambda: Hostile(speed=0.0), fake=True),
            out=out,
            err=err,
        )
    assert code == 1  # every run failed: the model said the token and then broke
    captured = capsys.readouterr()
    found = canary.find(
        [secret, secret[-12:]],
        logs=caplog.records,
        values=[
            out.getvalue(),
            err.getvalue(),
            captured.out,
            captured.err,
            path.read_text(),
            *seen,
        ],
    )
    assert found == []
    assert len(caplog.records) > 0  # there was something to look through
    assert canary.find([secret], values=[f"x {secret}"]) != []  # and the canary can see


def test_the_credential_is_in_no_table_of_the_database(
    make: Callable[..., Env],
    factory: sessionmaker[Session],
) -> None:
    secret = "sk-ant-oat01-" + secrets.token_urlsafe(48)
    env = make(
        cv_scripts(),
        credential=Credential(
            CredentialKind.claude_subscription, secret, "CLAUDE_CODE_OAUTH_TOKEN"
        ),
        backend=lambda: Hostile(speed=0.0),
    )
    measure.run_pair(env, ALEX, "cv", 0)
    with factory() as session:
        tables = session.scalars(
            sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).all()
        dump = [
            session.scalar(
                sa.text(
                    f"SELECT coalesce(string_agg(to_jsonb(t)::text, ' '), '') FROM \"{name}\" t"
                )
            )
            for name in tables
        ]
    assert any(dump)
    assert canary.find([secret, secret[-12:]], values=dump) == []
    other = OTHER_KEY  # the control: a planted value in the same dump is found
    assert canary.find([other], values=[*dump, other]) != []
