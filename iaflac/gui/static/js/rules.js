// The library rules flacmn on the phone uses (apps/flacmn-mobile/lib/library/rules.dart, from the Pi player's
// library.py), so Albums and Artists sort and group the same on the desktop and the phone.

// "Disc 10" after "Disc 9": text in lower case, runs of digits as numbers
function natural(s) {
  const out = [];
  let i = 0;
  for (const m of String(s).matchAll(/\d+/g)) {
    out.push(s.slice(i, m.index).toLowerCase(), BigInt(m[0]));
    i = m.index + m[0].length;
  }
  out.push(s.slice(i).toLowerCase());
  return out;
}

function compareNatural(a, b) {
  for (let i = 0; i < a.length && i < b.length; i++) {
    const x = a[i], y = b[i];
    const c = typeof x === "bigint" ? (x < y ? -1 : x > y ? 1 : 0) : x < y ? -1 : x > y ? 1 : 0;
    if (c) return c;
  }
  return a.length - b.length;
}

const LEADING_THE = /^the\s+/i;
const LEADING_PUNCT = /^[^\p{L}\p{M}\p{N}_]+/u;

/** Albums A to Z: by title, ignoring case, a leading "The" and punctuation at the start; numbers in order; ties by artist. */
export function compareTitleOrder(a, b) {
  const key = (t) => natural(String(t || "").trim().replace(LEADING_PUNCT, "").replace(LEADING_THE, ""));
  return compareNatural(key(a.album), key(b.album)) || compareNatural(natural(a.artist || ""), natural(b.artist || ""));
}

/** The library's order: by artist ("The" ignored), then year (undated last), then title. */
export function compareArtistOrder(a, b) {
  const artist = (s) => String(s || "").replace(LEADING_THE, "").toLowerCase();
  const cmp = (x, y) => (x < y ? -1 : x > y ? 1 : 0);
  return cmp(artist(a.artist), artist(b.artist)) || cmp(a.year || "9999", b.year || "9999")
    || cmp(String(a.album || "").toLowerCase(), String(b.album || "").toLowerCase());
}

/** An artist's name with case and accents folded away, so Bjork and Björk are one artist. */
export const plainName = (s) => String(s || "").toLowerCase().replace(/ß/g, "ss").trim().normalize("NFKD").replace(/\p{M}/gu, "");

/** [{name, albums: [entry]}]: one per artist however the tags accent it, named as most of its albums spell it, in the
 *  library's order. */
export function artistsOf(albums) {
  const groups = new Map();
  for (const a of [...albums].sort(compareArtistOrder)) {
    const k = plainName(a.artist);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(a);
  }
  return [...groups.values()].map((list) => {
    const counts = new Map();
    for (const a of list) counts.set(a.artist, (counts.get(a.artist) || 0) + 1);
    let best = list[0].artist;
    for (const [k, v] of counts) if (v > counts.get(best)) best = k;   // ties: the spelling seen first
    return { name: best, albums: list };
  });
}
