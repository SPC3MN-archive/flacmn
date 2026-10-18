// The Library's tag search. Plain words match any tag of an album: its artist and album, the track titles, genres,
// labels and catalogue numbers, guest artists, year, format and folder. "field:word" narrows a word to one of them
// (artist:, album:, song:, genre:, label:, year:, format:, folder:), and "quotes" keep words together. Every word must
// match; accents and case are ignored, so bjork finds Björk.

const FIELDS = {
  artist: (a) => [a.artist, ...(a.guests || [])],
  album: (a) => [a.album],
  song: (a) => a.songs || [],
  genre: (a) => a.genres || [],
  label: (a) => a.labels || [],
  year: (a) => [a.year],
  format: (a) => [a.format_label, ...Object.keys(a.formats || {})],
  folder: (a) => [a.folder],
};
const ALIAS = { track: "song", title: "song", songs: "song", catalog: "label", date: "year" };

export const fold = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

/** "genre:jazz \"so what\"" -> [{field: "genre", word: "jazz"}, {field: "", word: "so what"}] */
export function parseQuery(text) {
  const terms = [];
  for (const m of String(text || "").matchAll(/(?:(\w+):)?(?:"([^"]*)"?|(\S+))/g)) {
    let field = (m[1] || "").toLowerCase();
    field = ALIAS[field] || field;
    let word = m[2] ?? m[3] ?? "";
    if (field && !FIELDS[field]) { word = `${m[1]}:${word}`; field = ""; }     // "AC/DC: Live" is not a field
    word = fold(word).trim().replace(/:$/, "");                              // "AC/DC: Live" -> ac/dc, live
    if (word) terms.push({ field, word });
  }
  return terms;
}

/** null when the album doesn't match every term; else {song}: the first track title a word was found in, when the
 *  album's own names didn't have it (so the list can say why the album is there). */
export function matchAlbum(a, terms) {
  let song = "";
  for (const { field, word } of terms) {
    const fields = field ? [field] : Object.keys(FIELDS);
    let hit = false;
    for (const f of fields) {
      const found = FIELDS[f](a).find((v) => fold(v).includes(word));
      if (found === undefined) continue;
      hit = true;
      if (f === "song" && !song) song = found;
      if (f !== "song") break;          // a name match beats a song match: no song shown for it
    }
    if (!hit) return null;
  }
  return { song };
}
