"""secrets.scan: each rule fires on a value of its shape and stays quiet on ordinary prose.

Every value below is made up and assembled from pieces at run time, so that neither this file
nor a scanner of the repository ever holds a string with the shape of a real credential."""

import re
import time

import pytest

from app.ingestion import secrets
from app.ingestion.secrets import GENERIC_RULE, RULE_NAMES, entropy, scan

ALNUM = "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zC"  # 32 mixed characters, no meaning


def join(*parts: str) -> str:
    return "".join(parts)


SAMPLES = {
    "private-key": join("-----BEGIN RSA PRIV", "ATE KEY-----\nMIIEow"),
    "aws-access-key": join("AKIA", "Z7XQ2M4K", "T6WB3N5P"),
    "anthropic-key": join("sk-ant-", "api03-", ALNUM * 3),
    "openai-key": join("sk-proj-", ALNUM, "T3Blbk", "FJ", ALNUM),
    "github-token": join("ghp_", ALNUM, "aB3d"),
    "gitlab-token": join("glpat-", ALNUM[:20]),
    "slack-token": join("xoxb-", "1234567890-", ALNUM[:12]),
    "slack-webhook": join("https://hooks.slack.com/", "services/", ALNUM, ALNUM[:14]),
    "stripe-key": join("sk_live_", ALNUM[:24]),
    "google-api-key": join("AIza", ALNUM, "aB3"),
    "huggingface-token": join("hf_", "aBcDeFgHiJkLmNoPqRsTuVwXyZaBcDeFgH"),
    "npm-token": join("npm_", ALNUM, "aB3d"),
    "pypi-token": join("pypi-AgEIcHlwaS5vcmc", ALNUM * 2),
    "sendgrid-key": join("SG.", ALNUM[:22], ".", ALNUM, ALNUM[:11]),
    "json-web-token": join("eyJhbGciOiJIUzI1NiIs", ".", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", ".", ALNUM),
    "url-credentials": join("postgres://robin:", "s3cretPass", "@db.example.org:5432/app"),
    GENERIC_RULE: join("API_TOKEN = '", "q7Zk2Lm9Xw4Rt8Vb1Nc6", "'"),
}


def test_every_rule_has_a_sample() -> None:
    assert set(SAMPLES) == set(RULE_NAMES)
    assert len(RULE_NAMES) == len(set(RULE_NAMES))


@pytest.mark.parametrize("rule", RULE_NAMES)
def test_a_value_of_the_rules_shape_is_found(rule: str) -> None:
    text = f"Some notes before.\n{SAMPLES[rule]}\nAnd some after."
    assert rule in scan(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Robin Example, applied AI engineer in Nantes, robin@example.org, +33 6 00 00 00 00.",
        "I built the token refresh flow and the password reset page of the product.",
        "Skills: Python, TypeScript, PostgreSQL. Key achievement: release gating in CI.",
        "The API: a REST service with twelve routes, documented with OpenAPI.",
        "password: (the one you chose)",
        "token = a_placeholder_value",  # no digit: a word, not a key
        "key: aaaaaaaaaaaaaaaa1111",  # a digit but no entropy
        # (Not ".../docs/...": the CI plan's guard reads that as a path of this repository.)
        "See https://example.org/guides/authentication for the secret rotation policy.",
        "https://robin@example.org/profile",  # a user name, no password
        "Contact: mailto:robin@example.org",
        "AKIA is how an AWS key id starts, and AKIAshort is not one.",
        "ghp_tooshort",
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
        "-----BEGIN PGP PUBLIC KEY BLOCK-----",
        "The meeting is at 10:30 on 2026-10-10, room 4.",
    ],
)
def test_ordinary_text_is_not_a_secret(text: str) -> None:
    assert scan(text) == []


def test_several_rules_are_reported_once_each_in_rule_order() -> None:
    text = "\n".join(
        [SAMPLES["github-token"], SAMPLES["aws-access-key"], SAMPLES["aws-access-key"]]
    )
    assert scan(text) == ["aws-access-key", "github-token"]


def test_the_generic_rule_needs_a_keyword_an_assignment_and_a_random_value() -> None:
    value = "q7Zk2Lm9Xw4Rt8Vb1Nc6"
    for line in (
        f"password={value}",
        f'"client_secret": "{value}"',
        f"db_passwd := {value}",
        f"auth_token => '{value}'",
        f"API KEY: {value}",
    ):
        assert scan(line) == [GENERIC_RULE], line
    for line in (value, f"the value {value} alone", f"password {value}"):
        assert scan(line) == [], line


VALUE = "q7Zk2Lm9Xw4Rt8Vb1Nc6"


@pytest.mark.parametrize(
    "line",
    [
        # The four lines of a made up technical CV that the review saw flagged.
        "API: REST/GraphQL/gRPC-web2, OpenAPI3.1",
        "Authentication: OAuth2/OIDC/JWT-RS256, Keycloak",
        "REST API, FastAPI/Flask/Django4.2, PostgreSQL16",
        "Author: Camille_Durand-2023",
        # The keyword only as the start or the end of a longer word.
        f"Authentication: {VALUE}",
        f"AUTHOR: {VALUE}",
        f"Keycloak: {VALUE}",
        f"Keystone: {VALUE}",
        f"keyboard: {VALUE}",
        f"tokenizer: {VALUE}",
        f"Secretary: {VALUE}",
        f"Accessibility: {VALUE}",
        f"APIs: {VALUE}",
        f"OAuth: {VALUE}",
        f"FastAPI: {VALUE}",
        f"monkey: {VALUE}",
        f"Turkey = {VALUE}",
        # A comma is not an assignment.
        f"key, {VALUE}",
        f"Skills: API, {VALUE}",
        # A list of tools separated by slashes or plus signs is not one value.
        "token: Python3/TypeScript5/Go1.22",
        "Key: C++20/Rust1.75+LLVM17",
        "Stack (key): Next.js14/React18/Node22",
        # Ordinary lines of a CV or a dossier.
        "Key skills: Python 3.12, TypeScript 5, PostgreSQL 16",
        "Secret clearance: none. Token of appreciation: Employee of the Year 2023.",
        "Access: remote first since 2021, on site two days a month",
        "Key achievement: cut the release time from 3 days to 45 minutes",
        "Built the password reset flow and the API token rotation (2022 to 2024)",
    ],
)
def test_an_ordinary_line_of_a_technical_cv_is_not_a_secret(line: str) -> None:
    assert scan(line) == [], line


@pytest.mark.parametrize(
    "line",
    [
        f"password={VALUE}",
        f"Password: {VALUE}",
        f"PASSWORD = {VALUE}",
        f"db_password={VALUE}",
        f"DB_PASSWORD={VALUE}",
        f"dbPassword: '{VALUE}'",
        f'"clientSecret": "{VALUE}"',
        f"client-secret: {VALUE}",
        f"apiKey = {VALUE}",
        f"apikey={VALUE}",
        f"APIKEY={VALUE}",
        f"API_KEY={VALUE}",
        f"X-Api-Key: {VALUE}",
        f"access_token => {VALUE}",
        f"accessToken := {VALUE}",
        f"AUTH_TOKEN?={VALUE}",
        f"credentials: {VALUE}_x",
        f"tokens: {VALUE}",
        f"Passwords: {VALUE}",
        f"API_KEYS={VALUE}",
        f"Authorization: {VALUE}",
        f"authorization = {VALUE}",
        f"my.secret.key = {VALUE}",
        f"export OPENAI_API_KEY={VALUE}",
        f"token:{VALUE}",
    ],
)
def test_a_value_assigned_to_a_secret_name_is_still_caught(line: str) -> None:
    assert scan(line) == [GENERIC_RULE], line


def test_entropy() -> None:
    assert entropy("") == 0.0
    assert entropy("aaaa") == 0.0
    assert entropy("ab") == 1.0
    assert entropy("abcd") == 2.0
    assert entropy("q7Zk2Lm9Xw4Rt8Vb1Nc6") > secrets.GENERIC_ENTROPY


def test_only_rule_names_come_back_never_the_match() -> None:
    for rule, sample in SAMPLES.items():
        for name in scan(sample):
            assert name in RULE_NAMES
            assert re.fullmatch(r"[a-z-]+", name)
            assert sample not in name, rule


@pytest.mark.parametrize(
    "hostile",
    [
        "key" * 170_000,
        "password=" * 55_000,
        "token: " + "a1" * 250_000,
        "-----BEGIN " + "A" * 499_000,
        "ey" + "A" * 499_000,
        "a://" + "b:" * 250_000,
        ("api_key" + "." * 20 + " = ") * 16_000,
        "sk-" + "A" * 499_000,
        "secret=" + "aB3/" * 125_000,
        "mot de passe : " * 33_000,
        ("APIKey" + "-" * 20 + ": ") * 18_000,
    ],
    ids=[
        "key",
        "password",
        "token",
        "begin",
        "jwt",
        "url",
        "dotted",
        "sk",
        "base64",
        "fr",
        "mixed",
    ],
)
def test_a_hostile_text_of_half_a_million_characters_is_scanned_in_linear_time(
    hostile: str,
) -> None:
    start = time.perf_counter()
    scan(hostile[:500_000])
    # Generous for a loaded machine; a quadratic pattern would need minutes.
    assert time.perf_counter() - start < 5


# --- the second review: base64 values, the labels people write, labels that are also headings --

# 40 base64 characters with a slash and a plus, the form of an AWS secret key. The first is the
# example of the AWS documentation; both are assembled here, so no scanner reads a leak.
AWS_EXAMPLE = join("wJalrXUtnFEMI", "/K7MDENG/", "bPxRfiCYEXAMPLEKEY")
BASE64_VALUE = join("q7Zk2Lm9Xw4R", "/t8Vb1Nc6", "+Hd3Fg5Js0PaQeWrTyU")
# A password someone chose: words, hyphens and a year.
WORDS_VALUE = join("Winter-holi", "day-2024x")
ASSIGN = "="


@pytest.mark.parametrize(
    "line",
    [
        # A base64 value with a slash or a plus, after a name that can only be a secret's.
        join("AWS_SECRET_ACCESS_KEY", ASSIGN, AWS_EXAMPLE),
        join("aws_secret_access_key = ", AWS_EXAMPLE),
        join("export API_KEY", ASSIGN, BASE64_VALUE),
        join("password: ", BASE64_VALUE),
        join('"clientSecret": "', BASE64_VALUE, '"'),
        # The labels a French speaker writes, with the space French puts before the colon.
        f"Mot de passe : {VALUE}",
        f"mot de passe: {VALUE}",
        f"MOT DE PASSE = {VALUE}",
        f"Mot de passe\u00a0: {VALUE}",
        f"Mot de passe\u202f: {VALUE}",
        f"mdp={VALUE}",
        f"MDP : {VALUE}",
        f"Mdp: {VALUE}",
        # Short forms and mixed cases.
        f"pwd:{VALUE}",
        f"PWD={VALUE}",
        f"db_pwd = {VALUE}",
        f"APIKey = {VALUE}",
        f"ApiKey: {VALUE}",
        f"API-Key: {VALUE}",
        # A password is a secret even when it reads as words.
        f"password: {WORDS_VALUE}",
        f"Mot de passe : {WORDS_VALUE}",
        f"DB_PASSWORD={WORDS_VALUE}",
        # A name of several parts is a variable, not a heading: its value counts as before.
        f"access_token: {WORDS_VALUE}",
        f"X-Api-Key: {WORDS_VALUE}",
    ],
)
def test_the_secrets_the_second_review_saw_missed_are_caught(line: str) -> None:
    assert scan(line) == [GENERIC_RULE], line


@pytest.mark.parametrize(
    "line",
    [
        # The five ordinary lines the second review saw flagged: a keyword used as a heading,
        # then one hyphenated term with a digit. Two are put together here: written whole,
        # the scanner of this repository (gitleaks) takes them for keys, as this module did.
        "Auth: OAuth2-PKCE-OIDC-flows",
        "API: OpenAPI3.1-compliant",
        join("Token: JWT-RS", "256-rotation"),
        join("Key: Kubernetes1", ".29-certified (CKA)"),
        "Tokens: 128k-context-window",
        # The same heading before words that would be a password after "password".
        f"Token: {WORDS_VALUE}",
        # Lists of tools long enough, and varied enough, to pass for base64 by their entropy.
        "Key: PostgreSQL16/Elasticsearch8/MongoDB7",
        "API: Python3/TypeScript5/Golang/Rust/Kotlin2/Swift6",
        "Token: PyTorch2/TensorFlow2/JAX/HuggingFace/LangChain/LlamaIndex",
        "Auth: React18/Vue3/Angular17/Svelte4/NextJS14/Nuxt3",
        "Access: C++20/Rust1/LLVM17/CMake3/Conan2/Bazel7/Ninja",
        # A base64 value after a heading is left alone too: the price of the lines above.
        join("Key: ", BASE64_VALUE),
        # A file path after a sure label: slashes, a digit, never the entropy of a key.
        "password_file: /var/lib/postgresql16/secrets/password",
        "SECRET_KEY_FILE=/home/robin/projects2024/jobapp/secrets/apikey",
        "pwd: /home/robin/projects2024/jobapp",
        # The labels alone, or with an ordinary answer.
        "Mot de passe : (celui que vous avez choisi)",
        "Mot de passe oublie : utilisez le lien de la page de connexion",
        "mdp : voir avec Robin",
        "APIKey: demander au support",
        "MDP: value-iteration and policy-gradient methods",
    ],
)
def test_the_ordinary_lines_of_the_second_review_are_not_secrets(line: str) -> None:
    assert scan(line) == [], line


def test_a_base64_value_needs_its_length_and_its_entropy() -> None:
    assert len(AWS_EXAMPLE) == len(BASE64_VALUE) == 40
    assert entropy(AWS_EXAMPLE) > secrets.BASE64_ENTROPY
    # The lists of tools above are as varied as base64: the entropy alone would not tell them.
    assert entropy("PyTorch2/TensorFlow2/JAX/HuggingFace/LangChain/LlamaIndex") > 4.5
    # Shorter than BASE64_MIN_CHARS: only the run before the first slash is looked at, as before.
    short = BASE64_VALUE[: secrets.BASE64_MIN_CHARS - 1]
    assert scan(join("secret", ASSIGN, short)) == [GENERIC_RULE]  # 12 random characters lead it
    assert scan(join("secret", ASSIGN, "ab/", short[:20])) == []
    # Long enough, with slashes and a digit, but as regular as a path.
    assert scan(join("secret", ASSIGN, "abc1/" * 8)) == []
