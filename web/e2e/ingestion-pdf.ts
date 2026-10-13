import { createHash } from "node:crypto";

/**
 * PDF files written by hand for the ingestion journeys (task G2b): no binary is committed and
 * every text in them is made up. A page is lines of text with link annotations, or a picture
 * and nothing else (a scanned page). `protect` adds the standard security handler of PDF 1.4
 * (RC4, 128 bits, revision 3), as an office suite does when it restricts printing or copying:
 * with an empty user password anyone opens the file, with a real one nobody does without it.
 */
export interface PdfPage {
  /** Lines of text, top to bottom. */
  lines?: string[];
  /** Addresses of the page's link annotations. */
  links?: string[];
  /** A picture over the whole page. Lines, when given, are drawn under it. */
  scan?: boolean;
}

export interface Protection {
  /** "" restricts copying and printing only: the file still opens for everyone. */
  userPassword: string;
}

const PAD = Buffer.from(
  "28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A",
  "hex",
);
const KEY_BYTES = 16;
// Printing is allowed, copying text is not: what "restricted" means for most exported CVs.
const PERMISSIONS = -3904;

const md5 = (...parts: Uint8Array[]) =>
  createHash("md5").update(Buffer.concat(parts)).digest();

function rc4(key: Uint8Array, data: Uint8Array): Buffer {
  const state = Uint8Array.from({ length: 256 }, (_, index) => index);
  const at = (index: number) => state[index] as number;
  const swap = (a: number, b: number) => {
    const kept = at(a);
    state[a] = at(b);
    state[b] = kept;
  };
  let j = 0;
  for (let i = 0; i < 256; i++) {
    j = (j + at(i) + (key[i % key.length] as number)) & 255;
    swap(i, j);
  }
  const out = Buffer.alloc(data.length);
  let i = 0;
  j = 0;
  for (let n = 0; n < data.length; n++) {
    i = (i + 1) & 255;
    j = (j + at(i)) & 255;
    swap(i, j);
    out[n] = (data[n] as number) ^ at((at(i) + at(j)) & 255);
  }
  return out;
}

const padded = (password: string) =>
  Buffer.concat([Buffer.from(password, "latin1"), PAD]).subarray(0, 32);

const xored = (key: Uint8Array, value: number) =>
  Uint8Array.from(key, (byte) => byte ^ value);

interface Keys {
  key: Buffer;
  owner: Buffer;
  user: Buffer;
  id: Buffer;
}

/** Algorithms 2, 3 and 5 of the PDF reference, for revision 3. */
function keys(userPassword: string, ownerPassword: string, id: Buffer): Keys {
  let digest = md5(padded(ownerPassword));
  for (let round = 0; round < 50; round++) digest = md5(digest);
  const ownerKey = digest.subarray(0, KEY_BYTES);
  let owner = rc4(ownerKey, padded(userPassword));
  for (let round = 1; round <= 19; round++)
    owner = rc4(xored(ownerKey, round), owner);

  const permissions = Buffer.alloc(4);
  permissions.writeInt32LE(PERMISSIONS);
  digest = md5(padded(userPassword), owner, permissions, id);
  for (let round = 0; round < 50; round++)
    digest = md5(digest.subarray(0, KEY_BYTES));
  const key = Buffer.from(digest.subarray(0, KEY_BYTES));

  let user = rc4(key, md5(PAD, id));
  for (let round = 1; round <= 19; round++) user = rc4(xored(key, round), user);
  return { key, owner, user: Buffer.concat([user, Buffer.alloc(16)]), id };
}

const escaped = (text: string) =>
  text.replace(/[\\()]/g, (found) => `\\${found}`);

function content(page: PdfPage): Buffer {
  const parts: Buffer[] = [];
  if (page.lines?.length)
    parts.push(
      Buffer.from(
        `BT /F1 11 Tf 56 790 Td 15 TL ${page.lines.map((line) => `(${escaped(line)}) Tj T*`).join(" ")} ET\n`,
        "latin1",
      ),
    );
  if (page.scan)
    // One grey pixel stretched over the page: a picture, no text.
    parts.push(
      Buffer.concat([
        Buffer.from("q 595 0 0 842 0 0 cm BI /W 1 /H 1 /CS /G /BPC 8 ID "),
        Buffer.from([0x80]),
        Buffer.from(" EI Q\n"),
      ]),
    );
  return Buffer.concat(parts);
}

export function makePdf(pages: PdfPage[], protect?: Protection): Buffer {
  const id = md5(Buffer.from(JSON.stringify(pages), "utf8"));
  const secret = protect
    ? keys(protect.userPassword, "owner of the fixture", id)
    : undefined;
  /** What object `number` holds for these bytes: as they are, or encrypted with its own key. */
  const sealed = (number: number, data: Buffer): Buffer => {
    if (!secret) return data;
    const objectKey = md5(
      secret.key,
      Buffer.from([
        number & 255,
        (number >> 8) & 255,
        (number >> 16) & 255,
        0,
        0,
      ]),
    ).subarray(0, KEY_BYTES);
    return rc4(objectKey, data);
  };
  const hex = (data: Buffer) => `<${data.toString("hex")}>`;

  // 1 catalog, 2 page tree, 3 font, then a page and its content stream for each page.
  const objects: Buffer[] = [];
  const pageNumber = (index: number) => 4 + index * 2;
  objects.push(Buffer.from("<< /Type /Catalog /Pages 2 0 R >>"));
  objects.push(
    Buffer.from(
      `<< /Type /Pages /Kids [${pages.map((_, index) => `${pageNumber(index)} 0 R`).join(" ")}] /Count ${pages.length} >>`,
    ),
  );
  objects.push(
    Buffer.from(
      "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ),
  );
  pages.forEach((page, index) => {
    const number = pageNumber(index);
    const links = (page.links ?? [])
      .map(
        (address, position) =>
          `<< /Type /Annot /Subtype /Link /Rect [56 ${700 - position * 20} 300 ${715 - position * 20}] /Border [0 0 0] ` +
          `/A << /S /URI /URI ${hex(sealed(number, Buffer.from(address, "latin1")))} >> >>`,
      )
      .join(" ");
    objects.push(
      Buffer.from(
        `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents ${number + 1} 0 R ` +
          `/Resources << /Font << /F1 3 0 R >> >>${links ? ` /Annots [${links}]` : ""} >>`,
      ),
    );
    const stream = sealed(number + 1, content(page));
    objects.push(
      Buffer.concat([
        Buffer.from(`<< /Length ${stream.length} >>\nstream\n`),
        stream,
        Buffer.from("\nendstream"),
      ]),
    );
  });
  if (secret)
    objects.push(
      Buffer.from(
        `<< /Filter /Standard /V 2 /R 3 /Length ${KEY_BYTES * 8} /P ${PERMISSIONS} ` +
          `/O ${hex(secret.owner)} /U ${hex(secret.user)} >>`,
      ),
    );

  const chunks: Buffer[] = [
    Buffer.from("%PDF-1.4\n%\xe2\xe3\xcf\xd3\n", "latin1"),
  ];
  let length = chunks[0]?.length ?? 0;
  const offsets: number[] = [];
  objects.forEach((object, index) => {
    offsets.push(length);
    const chunk = Buffer.concat([
      Buffer.from(`${index + 1} 0 obj\n`),
      object,
      Buffer.from("\nendobj\n"),
    ]);
    chunks.push(chunk);
    length += chunk.length;
  });
  const size = objects.length + 1;
  const trailer =
    `<< /Size ${size} /Root 1 0 R` +
    (secret
      ? ` /Encrypt ${objects.length} 0 R /ID [${hex(secret.id)} ${hex(secret.id)}]`
      : "") +
    " >>";
  chunks.push(
    Buffer.from(
      `xref\n0 ${size}\n0000000000 65535 f \n` +
        offsets
          .map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`)
          .join("") +
        `trailer\n${trailer}\nstartxref\n${length}\n%%EOF\n`,
    ),
  );
  return Buffer.concat(chunks);
}

/** A real file cut at 60 percent: no cross reference table, its last object half written. */
export function cutPdf(whole: Buffer): Buffer {
  return whole.subarray(0, Math.floor(whole.length * 0.6));
}
