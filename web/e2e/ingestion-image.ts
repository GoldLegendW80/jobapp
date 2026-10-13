import { deflateSync } from "node:zlib";

/**
 * Pictures and scanned PDFs for the journeys of the ingestion module, built in code: no binary
 * fixture is kept in the repository, and nothing here was ever a real document.
 */

const CRC = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(data: Buffer): number {
  let c = 0xffffffff;
  for (const byte of data) c = (CRC[(c ^ byte) & 0xff] ?? 0) ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(kind: string, content: Buffer): Buffer {
  const body = Buffer.concat([Buffer.from(kind, "latin1"), content]);
  const head = Buffer.alloc(4);
  head.writeUInt32BE(content.length);
  const tail = Buffer.alloc(4);
  tail.writeUInt32BE(crc32(body));
  return Buffer.concat([head, body, tail]);
}

function png(width: number, height: number, depth: 1 | 8, rows: Buffer) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = depth; // bits per sample, then colour type 0: grey
  return Buffer.concat([
    Buffer.from("\x89PNG\r\n\x1a\n", "latin1"),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(rows)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** Grey pixels that look like lines of text on a page: dark bars on a light ground. */
function lines(width: number, height: number, salt = 0): Buffer[] {
  return Array.from({ length: height }, (_, y) => {
    const row = Buffer.alloc(width, 242);
    const line = Math.floor(y / 18);
    if (y > 30 && y < height - 30 && y % 18 < 7) {
      const end =
        width - 40 - ((line * 37 + salt * 53) % Math.floor(width / 3));
      for (let x = 40; x < end; x++)
        if ((x + line * 7 + salt) % 11 > 1) row[x] = 40;
    }
    return row;
  });
}

/** A screenshot of a fragment of a CV: a grey PNG of `width` by `height` pixels. */
export function makeScreenshot(width = 800, height = 500, salt = 0): Buffer {
  const rows = lines(width, height, salt).map((row) =>
    Buffer.concat([Buffer.from([0]), row]),
  );
  return png(width, height, 8, Buffer.concat(rows));
}

/**
 * A PNG whose header declares `width` by `height` pixels, blank, one bit a pixel: a few
 * kilobytes of file for as many megapixels as the test needs.
 */
export function makeBlankPng(width: number, height: number): Buffer {
  return png(
    width,
    height,
    1,
    Buffer.alloc((Math.ceil(width / 8) + 1) * height),
  );
}

export type ScanPage = "scan" | string[];

/**
 * A PDF of A4 pages. A page given as "scan" holds one picture over the whole sheet and no
 * text layer, as a scanner writes it; a page given as lines holds those lines as text.
 */
export function makeScanPdf(pages: ScanPage[]): Buffer {
  const [width, height] = [310, 438];
  const picture = deflateSync(Buffer.concat(lines(width, height)));
  const objects: Buffer[] = [];
  const add = (body: string | Buffer) => objects.push(Buffer.from(body));
  const stream = (dictionary: string, content: Buffer) =>
    Buffer.concat([
      Buffer.from(
        `<< ${dictionary} /Length ${String(content.length)} >>\nstream\n`,
      ),
      content,
      Buffer.from("\nendstream"),
    ]);
  const kids = pages.map((_, n) => `${String(5 + 2 * n)} 0 R`).join(" ");
  add("<< /Type /Catalog /Pages 2 0 R >>");
  add(`<< /Type /Pages /Kids [${kids}] /Count ${String(pages.length)} >>`);
  add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>");
  add(
    stream(
      `/Type /XObject /Subtype /Image /Width ${String(width)} /Height ${String(height)} ` +
        "/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode",
      picture,
    ),
  );
  pages.forEach((page, n) => {
    const content =
      page === "scan"
        ? "q 595 0 0 842 0 0 cm /Im0 Do Q"
        : `BT /F1 11 Tf 14 TL 50 790 Td ${page
            .map((line) => `(${line.replace(/[\\()]/g, "\\$&")}) Tj T*`)
            .join(" ")} ET`;
    add(
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] " +
        "/Resources << /Font << /F1 3 0 R >> /XObject << /Im0 4 0 R >> >> " +
        `/Contents ${String(6 + 2 * n)} 0 R >>`,
    );
    add(stream("", Buffer.from(content, "latin1")));
  });
  const parts: Buffer[] = [
    Buffer.from("%PDF-1.4\n%\xe2\xe3\xcf\xd3\n", "latin1"),
  ];
  const offsets: number[] = [];
  let at = parts[0]?.length ?? 0;
  objects.forEach((body, n) => {
    const whole = Buffer.concat([
      Buffer.from(`${String(n + 1)} 0 obj\n`),
      body,
      Buffer.from("\nendobj\n"),
    ]);
    offsets.push(at);
    at += whole.length;
    parts.push(whole);
  });
  const count = objects.length + 1;
  const table = offsets
    .map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`)
    .join("");
  parts.push(
    Buffer.from(
      `xref\n0 ${String(count)}\n0000000000 65535 f \n${table}` +
        `trailer\n<< /Size ${String(count)} /Root 1 0 R >>\nstartxref\n${String(at)}\n%%EOF\n`,
    ),
  );
  return Buffer.concat(parts);
}
