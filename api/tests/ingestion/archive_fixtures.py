"""Word documents, LinkedIn like exports and hostile archives, built in code for the tests of
task G7 (no binary file is committed): tests/ingestion/parser/test_docx.py, test_linkedin_zip.py
and test_archive_container.py, and the worker's tests/ingestion/test_jobs_archive.py.

Every identity in them is made up (`example.org`).
"""

import io
import random
import struct
import warnings
import zipfile
from collections.abc import Callable, Iterable, Mapping

MIB = 1024 * 1024
NAMESPACES = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:v="urn:schemas-microsoft-com:vml"'
)
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/></Types>'
)
MACRO_CONTENT_TYPES = CONTENT_TYPES.replace(
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.ms-word.document.macroEnabled.main+xml",
)
LINK = "https://www.example.org/in/camille-durand"

Entries = Iterable[tuple[str, bytes | str]]


def zip_of(entries: Entries, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    """A ZIP of `entries`, in their order, with the names exactly as given."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # a name twice, on purpose
        for name, content in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = compression
            archive.writestr(info, content)
    return buffer.getvalue()


def words(count: int, seed: int = 1) -> str:
    """`count` made up words: text that deflates as text does, not as a bomb."""
    noise = random.Random(seed)  # noqa: S311, a fixture, not a secret
    letters = "abcdefghijklmnopqrstuvwxyz"
    return " ".join("".join(noise.choices(letters, k=noise.randint(2, 9))) for _ in range(count))


# --- Word documents -----------------------------------------------------------------------------


def run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def paragraph(*inside: str) -> str:
    return f"<w:p>{''.join(inside)}</w:p>"


def table(*rows: tuple[str, ...]) -> str:
    cells = ("".join(f"<w:tc>{paragraph(run(cell))}</w:tc>" for cell in row) for row in rows)
    return f"<w:tbl>{''.join(f'<w:tr>{row}</w:tr>' for row in cells)}</w:tbl>"


def part(root: str, *inside: str, prolog: str = "") -> str:
    """One XML part: `root` is `w:document`, `w:hdr` or `w:ftr`."""
    body = "".join(inside)
    if root == "w:document":
        body = f"<w:body>{body}</w:body>"
    return f'<?xml version="1.0" encoding="UTF-8"?>{prolog}<{root} {NAMESPACES}>{body}</{root}>'


def relationships(links: Mapping[str, str], mode: str = "External") -> str:
    rows = "".join(
        f'<Relationship Id="{key}" Type="http://schemas.openxmlformats.org/officeDocument/'
        f'2006/relationships/hyperlink" Target="{target}" TargetMode="{mode}"/>'
        for key, target in links.items()
    )
    return (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rows}</Relationships>"
    )


def docx(
    *inside: str,
    links: Mapping[str, str] | None = None,
    headers: Iterable[str] = (),
    footers: Iterable[str] = (),
    extra: Entries = (),
    content_types: str = CONTENT_TYPES,
    document: str | None = None,
) -> bytes:
    """A Word document whose body holds `inside`. `document` replaces the whole XML of the
    body part; `headers` and `footers` are the texts of `word/header1.xml` and so on."""
    entries: list[tuple[str, bytes | str]] = [
        ("[Content_Types].xml", content_types),
        ("word/document.xml", document if document is not None else part("w:document", *inside)),
    ]
    if links is not None:
        entries.append(("word/_rels/document.xml.rels", relationships(links)))
    for kind, root, texts in (("header", "w:hdr", headers), ("footer", "w:ftr", footers)):
        for number, text in enumerate(texts, 1):
            entries.append((f"word/{kind}{number}.xml", part(root, paragraph(run(text)))))
    return zip_of([*entries, *extra])


def hyperlink(text: str, relationship: str = "", anchor: str = "") -> str:
    attributes = (f' r:id="{relationship}"' if relationship else "") + (
        f' w:anchor="{anchor}"' if anchor else ""
    )
    return f"<w:hyperlink{attributes}>{run(text)}</w:hyperlink>"


def text_box(text: str) -> str:
    """A text box as Word writes it: the drawing, and the same text again as its fallback."""
    content = f"<w:txbxContent>{paragraph(run(text))}</w:txbxContent>"
    return (
        "<w:r><mc:AlternateContent>"
        f'<mc:Choice Requires="wps"><wps:txbx>{content}</wps:txbx></mc:Choice>'
        f"<mc:Fallback><w:pict><v:textbox>{content}</v:textbox></w:pict></mc:Fallback>"
        "</mc:AlternateContent></w:r>"
    )


def cv_docx() -> bytes:
    """The document of the journeys: a heading, a link shown as a word, a table."""
    return docx(
        paragraph(run("Camille Durand")),
        paragraph(run("Find me on "), hyperlink("LinkedIn", "rId1")),
        table(("Language", "Level"), ("French", "Native")),
        links={"rId1": LINK},
        headers=["Curriculum vitae"],
        footers=["camille.durand@example.org"],
    )


def images_docx(size: int = 20 * MIB - 4096) -> bytes:
    """A document of about `size` bytes that is almost all images, stored as they are, and one
    line of text."""
    noise = random.Random(7).randbytes(size // 4)  # noqa: S311, a fixture, not a secret
    extra = [(f"word/media/image{number}.jpeg", b"\xff\xd8\xff" + noise) for number in range(4)]
    body = part("w:document", paragraph(run("A portfolio of four photographs.")))
    return zip_of(
        [
            ("[Content_Types].xml", CONTENT_TYPES),
            ("word/document.xml", body),
            *extra,
        ],
        zipfile.ZIP_STORED,
    )


ENTITY_BOMB = (
    '<!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa">'
    + "".join(
        f'<!ENTITY {new} "{";".join(["&" + old] * 10)};">'
        for old, new in zip("abcdefg", "bcdefgh", strict=True)
    )
    + "]>"
)
EXTERNAL_ENTITY = '<!DOCTYPE d [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'


def entity_bomb_docx() -> bytes:
    return docx(document=part("w:document", paragraph(run("&h;")), prolog=ENTITY_BOMB))


def external_entity_docx() -> bytes:
    return docx(document=part("w:document", paragraph(run("&secret;")), prolog=EXTERNAL_ENTITY))


def distinct_names_docx(what: str = "element", size: int = 19 * MIB) -> bytes:
    """A body of `size` bytes in which every name is new: the parser keeps each distinct name
    it meets for as long as it lives, so this is what a Word document can cost it most. It
    packs about four times, so every cap of the archive passes. `what` is where the names
    are: one "element" each, one "attribute" each on elements of one name, or all of them as
    the attributes of "one tag" (which the walker is told of only when the tag is complete)."""
    if what == "element":
        body = "".join(f"<q{number:08x}/>" for number in range(size // 12))
    elif what == "attribute":
        body = "".join(f'<w:r a{number:08x}=""/>' for number in range(size // 19))
    else:
        names = " ".join(f'a{number:08x}=""' for number in range(size // 13))
        body = f"<w:r {names}/>"
    return docx(paragraph(run("The first line.")), body)


def deep_docx(size: int = 19 * MIB, vocabulary: int = 2000) -> bytes:
    """A body of `size` bytes of elements each inside the last, their names drawn from a small
    vocabulary: well formed, under the ratio cap, and every open element is kept by the
    parser until it ends."""
    noise = random.Random(11)  # noqa: S311, a fixture, not a secret
    names = noise.choices([f"w:n{number}" for number in range(vocabulary)], k=size // 20)
    opened = "".join(f"<{name}>" for name in names)
    closed = "".join(f"</{name}>" for name in reversed(names))
    return docx(paragraph(run("The first line.")), opened + closed)


# --- LinkedIn like exports ----------------------------------------------------------------------

POSITIONS = (
    "Company Name,Title,Description,Location,Started On,Finished On\r\n"
    'Example Rail,Data engineer,"Pipelines, in Python",Lyon,Mar 2021,\r\n'
)
PROFILE = "First Name,Last Name,Headline\r\nCamille,Durand,Data engineer\r\n"
SKILLS = "Name\r\nPython\r\nSQL\r\n"


def export(files: Mapping[str, bytes | str] | None = None) -> bytes:
    """A LinkedIn like export: three known CSV files at the root, and entries nobody reads."""
    if files is None:
        files = {
            "Connections.csv": "First Name,Last Name\r\nSomeone,Else\r\n",
            "Positions.csv": POSITIONS,
            "messages.csv": "FROM,TO,CONTENT\r\nSomeone,Camille,never read\r\n",
            "Profile.csv": PROFILE,
            "Skills.csv": SKILLS,
            "Jobs/Saved Jobs.csv": "Title\r\nnever read\r\n",
        }
    return zip_of(files.items())


# --- hostile archives ---------------------------------------------------------------------------


def _sparse(size: int, seed: int) -> bytes:
    """`size` bytes that deflate about fifty times: under the ratio cap, far over nothing."""
    noise = random.Random(seed)  # noqa: S311, a fixture, not a secret
    chunk = 4096
    return b"".join(noise.randbytes(64) + bytes(chunk - 64) for _ in range(size // chunk))


def ratio_bomb() -> bytes:
    """One entry of 8 MiB of zeros: a ratio of about a thousand."""
    return zip_of([("[Content_Types].xml", CONTENT_TYPES), ("word/document.xml", bytes(8 * MIB))])


def total_size_bomb() -> bytes:
    """Three entries of 19 MiB, each under the entry cap and under the ratio cap: 57 MiB."""
    entries = [(f"word/media/image{number}.bin", _sparse(19 * MIB, number)) for number in range(3)]
    return zip_of([("[Content_Types].xml", CONTENT_TYPES), *entries])


def too_many_entries(count: int = 201) -> bytes:
    return zip_of((f"Profile {number}.csv", "a,b\r\n") for number in range(count))


def nested_archive() -> bytes:
    return zip_of([("Positions.csv", POSITIONS), ("inner.zip", zip_of([("a.txt", "a")]))])


def nested_archive_unnamed() -> bytes:
    """An archive inside the archive under the name of a file that is read."""
    inner = zip_of([("a.txt", "a")], zipfile.ZIP_STORED)
    return zip_of(
        [
            ("[Content_Types].xml", inner),
            ("word/document.xml", part("w:document")),
            ("Positions.csv", inner),
        ],
        zipfile.ZIP_STORED,
    )


def lying_entry(name: str = "word/document.xml") -> bytes:
    """`name` declares 100,000 bytes and inflates to 6 MiB: its two headers were rewritten."""
    real = part("w:document", paragraph(run("x"))).encode() + _sparse(6 * MIB, 3)
    data = bytearray(zip_of([("[Content_Types].xml", CONTENT_TYPES), (name, real)]))
    declared = struct.pack("<I", 100_000)
    local = data.find(b"PK\x03\x04", 4)
    central = data.find(b"PK\x01\x02", data.find(b"PK\x01\x02") + 4)
    data[local + 22 : local + 26] = declared
    data[central + 24 : central + 28] = declared
    return bytes(data)


def path_out() -> bytes:
    return zip_of(
        [
            ("[Content_Types].xml", CONTENT_TYPES),
            ("word/document.xml", part("w:document", paragraph(run("text")))),
            ("Positions.csv", POSITIONS),
            ("../../etc/cron.d/job", "* * * * * root true\n"),
        ]
    )


# (name, the file, the code both readers must answer). Each ends as a code of the closed
# list, with nothing written anywhere, in the test process and in the container.
HOSTILE: list[tuple[str, Callable[[], bytes], str]] = [
    ("a ratio bomb", ratio_bomb, "bomb"),
    ("a total size bomb", total_size_bomb, "bomb"),
    ("201 entries", too_many_entries, "bomb"),
    ("a nested archive", nested_archive, "nested_archive"),
    ("a nested archive under a read name", nested_archive_unnamed, "nested_archive"),
    ("an entry lying about its size", lying_entry, "bomb"),
    ("a path with ..", path_out, "unreadable"),
]
