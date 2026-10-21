/** Unicode's Default_Ignorable_Code_Point ranges, as the api's `chat.visible` reads them. */
const IGNORABLE: readonly (readonly [number, number])[] = [
  [0x00ad, 0x00ad],
  [0x034f, 0x034f],
  [0x061c, 0x061c],
  [0x115f, 0x1160],
  [0x17b4, 0x17b5],
  [0x180b, 0x180f],
  [0x200b, 0x200f],
  [0x202a, 0x202e],
  [0x2060, 0x206f],
  [0x3164, 0x3164],
  [0xfe00, 0xfe0f],
  [0xfeff, 0xfeff],
  [0xffa0, 0xffa0],
  [0xfff0, 0xfff8],
  [0x1bca0, 0x1bca3],
  [0x1d173, 0x1d17a],
  [0xe0000, 0xe0fff],
];

const BLANK = /\s/u;

/** Whether a message holds a character that is drawn: one neither blank nor ignorable. */
export function drawn(text: string): boolean {
  for (const ch of text) {
    const point = ch.codePointAt(0) ?? 0;
    if (BLANK.test(ch)) continue;
    if (IGNORABLE.some(([from, to]) => point >= from && point <= to)) continue;
    return true;
  }
  return false;
}
