"""The caps every archive is opened under (task G7 of the ingestion plan): the one way docx.py
and linkedin_zip.py look into a ZIP. Not a handler: main.py puts this directory on `sys.path`
after the lockdown, and the two handlers import this file from beside them.

The bytes are hostile. Nothing is ever extracted: an entry is inflated in memory, only when a
handler asks for it by its name, and a name is only ever compared with the names a handler
knows. No name becomes a path.

Checked on the central directory, before any entry is read (`Archive(data)`):

    ENTRIES_MAX  entries, counted on the end record before the directory is parsed and again
                 on what was parsed. An archive whose end record is ZIP64 is refused (nothing
                 under 20 MiB needs one). An entry that carries a ZIP64 extra field in an
                 ordinary archive is not: it is read, and the caps below are applied to the
                 sizes that field gives, which are the sizes then used
    ENTRY_MAX    bytes one entry may declare, TOTAL_MAX for all of them together
    RATIO_MAX    declared size over packed size, per entry
    no nested archive (told by the name of an entry), no encrypted entry, no other packing
    than stored and deflated, no name twice, no name that is a path out (absolute, `..`, a
    backslash, a drive, a NUL)

Checked again while reading (`Archive.read(name)`): the entry inflates to exactly the size it
declared, one byte more and it lied; its checksum holds; it does not start as an archive; the
bytes read so far stay under TOTAL_MAX.

What is refused is refused with a code of parser_client.FILE_CODES, never a sentence:

    unreadable      not a ZIP, damaged, cut short, a name twice, a path out, an unknown packing
    bomb            a cap above was reached, or an entry holds more than it declared
    nested_archive  an archive inside the archive
    protected       an encrypted entry
"""

import io
import struct
import zipfile
import zlib

MIB = 1024 * 1024
ENTRIES_MAX = 200
TOTAL_MAX = 50 * MIB
ENTRY_MAX = 20 * MIB
RATIO_MAX = 100
# The whole central directory: 200 entries with names of a few dozen bytes are some kilobytes.
DIRECTORY_MAX = ENTRIES_MAX * 4096

_END = b"PK\x05\x06"  # end of central directory
_END_SIZE = 22
_END_SEARCH = _END_SIZE + 65_535  # the record, and the longest comment that may follow it
_ZIP64_LOCATOR = b"PK\x06\x07"
_LOCAL = b"PK\x03\x04"
_LOCAL_SIZE = 30
# An entry that is itself an archive, by its name: read by nothing here, and refused so that a
# file cannot carry what was never looked at through this door.
NESTED_SUFFIXES = (
    ".zip", ".docx", ".docm", ".dotx", ".dotm", ".xlsx", ".xlsm", ".pptx", ".pptm", ".odt",
    ".ods", ".odp", ".jar", ".war", ".apk", ".epub", ".7z", ".rar", ".gz", ".tgz", ".bz2",
    ".xz", ".zst", ".tar", ".cab", ".iso",
)  # fmt: skip
# And by its first bytes, for an entry that is read: ZIP, gzip, 7z, RAR, xz. Not bzip2, which
# is told by its name alone (`.bz2` above): under another name its bytes are read as text.
_NESTED_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b", b"7z\xbc\xaf", b"Rar!", b"\xfd7zXZ")


class Refused(Exception):  # noqa: N818, an outcome, not an error of this program
    """The archive is not read, for the reason `code` (a code of parser_client.FILE_CODES)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _check_end_record(data: bytes) -> None:
    """The end record says how many entries follow and how long their directory is: both are
    capped here, before zipfile builds an object for each entry it finds."""
    tail = data[-_END_SEARCH:]
    at = tail.rfind(_END)
    if at < 0 or len(tail) - at < _END_SIZE:
        raise Refused("unreadable")
    entries, directory_size, directory_offset = struct.unpack("<HII", tail[at + 10 : at + 20])
    if entries == 0xFFFF or 0xFFFFFFFF in (directory_size, directory_offset):
        raise Refused("bomb")  # ZIP64
    if tail[max(at - 20, 0) : at][:4] == _ZIP64_LOCATOR:
        raise Refused("bomb")  # ZIP64 records zipfile would believe over the end record
    if entries > ENTRIES_MAX or directory_size > DIRECTORY_MAX:
        raise Refused("bomb")


def _is_path_out(name: str) -> bool:
    return (
        not name
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
        or (len(name) > 1 and name[1] == ":")
        or ".." in name.split("/")
    )


class Archive:
    """A ZIP whose central directory passed every cap. `names` maps the name of each file entry
    (directories left out) to its record; `read` inflates one entry in memory."""

    def __init__(self, data: bytes) -> None:
        if not data.startswith(_LOCAL):
            raise Refused("unreadable")
        _check_end_record(data)
        try:
            listing = zipfile.ZipFile(io.BytesIO(data)).infolist()
        except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError, EOFError) as error:
            raise Refused("unreadable") from error
        except (NotImplementedError, struct.error) as error:
            raise Refused("unreadable") from error
        if len(listing) > ENTRIES_MAX:
            raise Refused("bomb")
        self.data = data
        self.names: dict[str, zipfile.ZipInfo] = {}
        self.read_bytes = 0
        total = 0
        for info in listing:
            name = info.orig_filename
            if _is_path_out(name):
                raise Refused("unreadable")
            if name.endswith("/"):
                continue
            if name in self.names:
                raise Refused("unreadable")
            if info.flag_bits & 0x1:
                raise Refused("protected")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise Refused("unreadable")
            total += info.file_size
            if info.file_size > ENTRY_MAX or total > TOTAL_MAX:
                raise Refused("bomb")
            if info.file_size > RATIO_MAX * max(info.compress_size, 1):
                raise Refused("bomb")
            if name.lower().endswith(NESTED_SUFFIXES):
                raise Refused("nested_archive")
            self.names[name] = info

    def read(self, name: str) -> bytes:
        """The bytes of the entry `name`, inflated in memory, never more than it declared."""
        info = self.names[name]
        start = info.header_offset
        head = self.data[start : start + _LOCAL_SIZE]
        if len(head) < _LOCAL_SIZE or head[:4] != _LOCAL:
            raise Refused("unreadable")
        name_length, extra_length = struct.unpack("<HH", head[26:30])
        begin = start + _LOCAL_SIZE + name_length + extra_length
        packed = memoryview(self.data)[begin : begin + info.compress_size]
        if len(packed) != info.compress_size:
            raise Refused("unreadable")
        if info.compress_type == zipfile.ZIP_STORED:
            if info.compress_size != info.file_size:
                raise Refused("bomb")
            content = bytes(packed)
        else:
            inflater = zlib.decompressobj(-zlib.MAX_WBITS)
            try:
                # One byte more than declared is all a lying entry is given room for.
                content = inflater.decompress(packed, info.file_size + 1)
            except zlib.error as error:
                raise Refused("unreadable") from error
            if len(content) > info.file_size or inflater.unconsumed_tail:
                raise Refused("bomb")
            if len(content) < info.file_size or not inflater.eof:
                raise Refused("unreadable")
        if zlib.crc32(content) != info.CRC:
            raise Refused("unreadable")
        if content.startswith(_NESTED_MAGICS):
            raise Refused("nested_archive")
        self.read_bytes += len(content)
        if self.read_bytes > TOTAL_MAX:
            raise Refused("bomb")
        return content
