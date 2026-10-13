import { deflateRawSync } from "node:zlib";

/**
 * ZIP archives written by hand for the journeys of the ingestion module (task G7): a Word
 * document, a LinkedIn like export and a zip bomb, built in code so that no binary file is
 * committed. Every identity in them is made up.
 */
export interface ZipEntry {
  name: string;
  content: string | Buffer;
}

const CRC_TABLE = Array.from({ length: 256 }, (_, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit += 1)
    value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  return value >>> 0;
});

function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of data) crc = CRC_TABLE[(crc ^ byte) & 0xff]! ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/** A ZIP of `entries`, each deflated, in their order. */
export function makeZip(entries: ZipEntry[]): Buffer {
  const locals: Buffer[] = [];
  const central: Buffer[] = [];
  let offset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.name, "utf8");
    const content = Buffer.from(entry.content);
    const packed = deflateRawSync(content);
    const sizes = Buffer.alloc(12);
    sizes.writeUInt32LE(crc32(content), 0);
    sizes.writeUInt32LE(packed.length, 4);
    sizes.writeUInt32LE(content.length, 8);
    // version 20, UTF-8 names, deflate, no time, no date
    const common = Buffer.from([20, 0, 0, 8, 8, 0, 0, 0, 0, 0]);
    const lengths = Buffer.alloc(4);
    lengths.writeUInt16LE(name.length, 0);
    const local = Buffer.concat([
      Buffer.from("PK\x03\x04", "latin1"),
      common,
      sizes,
      lengths,
      name,
      packed,
    ]);
    const where = Buffer.alloc(4);
    where.writeUInt32LE(offset, 0);
    central.push(
      Buffer.concat([
        Buffer.from("PK\x01\x02", "latin1"),
        Buffer.from([20, 0]),
        common,
        sizes,
        lengths,
        Buffer.alloc(10), // comment length, disk, internal and external attributes
        where,
        name,
      ]),
    );
    locals.push(local);
    offset += local.length;
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.write("PK\x05\x06", 0, "latin1");
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, directory, end]);
}

const NAMESPACES =
  'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' +
  'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';
const CONTENT_TYPES =
  '<?xml version="1.0" encoding="UTF-8"?>' +
  '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
  '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-' +
  'officedocument.wordprocessingml.document.main+xml"/></Types>';

const run = (text: string) =>
  `<w:r><w:t xml:space="preserve">${text}</w:t></w:r>`;

export interface DocxLine {
  text: string;
  /** Words shown as a link, and the address the link goes to. */
  link?: { shown: string; address: string };
}

/** A Word document: one paragraph per line, a line's link stored as Word stores it. */
export function makeDocx(lines: DocxLine[]): Buffer {
  const links = lines.flatMap((line) => (line.link ? [line.link] : []));
  const paragraphs = lines.map((line) => {
    const index = line.link ? links.indexOf(line.link) + 1 : 0;
    const link = line.link
      ? `<w:hyperlink r:id="rId${index}">${run(line.link.shown)}</w:hyperlink>`
      : "";
    return `<w:p>${run(line.text)}${link}</w:p>`;
  });
  const relationships = links.map(
    (link, index) =>
      `<Relationship Id="rId${index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/` +
      `2006/relationships/hyperlink" Target="${link.address}" TargetMode="External"/>`,
  );
  return makeZip([
    { name: "[Content_Types].xml", content: CONTENT_TYPES },
    {
      name: "word/document.xml",
      content: `<?xml version="1.0" encoding="UTF-8"?><w:document ${NAMESPACES}><w:body>${paragraphs.join("")}</w:body></w:document>`,
    },
    {
      name: "word/_rels/document.xml.rels",
      content: `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${relationships.join("")}</Relationships>`,
    },
  ]);
}

/** A zip bomb: a Word document whose body is 8 MiB of nothing, a few kilobytes on the wire. */
export function makeZipBomb(): Buffer {
  return makeZip([
    { name: "[Content_Types].xml", content: CONTENT_TYPES },
    { name: "word/document.xml", content: Buffer.alloc(8 * 1024 * 1024) },
  ]);
}
