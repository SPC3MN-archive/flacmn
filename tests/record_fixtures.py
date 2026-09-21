"""Dev tool (needs network): snapshot the calibration items into tests/fixtures/*.json.

    python -m tests.record_fixtures

Stores trimmed item metadata, parsed FLAC headers (not audio) and only the log lines the parser
reads, so the provenance tests run offline against real-world data.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from iaflac.ia_client import IAClient
from iaflac.model import file_kind
from iaflac.pipeline import Pipeline
from iaflac.provenance import decode_text

FIXTURES = Path(__file__).parent / "fixtures"
CALIBRATION = {
    "mars_volta": "the-mars-volta-2005-frances-the-mute-075021039773-2005-02-21",
    "ff8_ost": "final-fantasy-viii-ost",
    "hunting_party": "thehuntingpartflac",
    "tanox_netlabel": "pn026",
}
KEEP_TAGS = ("ARTIST", "ALBUMARTIST", "ALBUM", "DATE", "GENRE", "ENCODER", "COMMENT", "DISCID",
             "MUSICBRAINZ_DISCID", "SOURCEMEDIA", "MEDIA", "ACCURATERIPRESULT", "ACCURATERIPDISCID")
LOG_LINES = re.compile(
    r"exact audio copy|x lossless decoder|dbpoweramp|whipper|accurate|no errors|there were errors|"
    r"copy ok|track quality|test crc|copy crc|read error|skipped|damaged sector|suspicious|"
    r"cdda|mpeg", re.I)


def trim_metadata(item) -> dict:
    meta = {"identifier": item.identifier, "title": item.title, "creator": item.creator,
            "date": item.date, "description": item.description[:1500], "collection": item.collections,
            "subject": item.subjects, "licenseurl": item.licenseurl, **item.extra}
    keep = [f for f in item.files if file_kind(f.name) in ("flac", "log", "cue", "accurip", "sfv", "md5",
                                                            "aucdtect", "text") and f.source != "derivative"
            or file_kind(f.name) == "flac"]
    files = [{"name": f.name, "format": f.format, "source": f.source, "size": str(f.size),
              "length": None if f.length is None else str(f.length)} for f in keep]
    return {"metadata": meta, "files": files, "item_size": item.item_size}


def relevant(text: str, limit: int = 120) -> str:
    return "\n".join([ln.strip() for ln in text.splitlines() if LOG_LINES.search(ln)][:limit])


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    pipe = Pipeline(IAClient())
    for key, ident in CALIBRATION.items():
        cand = pipe.load(ident)
        pipe.probe(cand)
        logs, aucdtect = {}, {}
        for f in cand.item.files_of("log")[:4]:
            logs[f.name] = relevant(decode_text(pipe.client.get_bytes(ident, f.name, 300_000)))
        for f in cand.item.files_of("aucdtect")[:4]:
            aucdtect[f.name] = decode_text(pipe.client.get_bytes(ident, f.name, 20_000))[:4000]
        probes = {}
        for name, p in cand.probes.items():
            d = dataclasses.asdict(p)
            d["tags"] = {k: v[:300] for k, v in p.tags.items() if k in KEEP_TAGS}
            probes[name] = d
        out = {"identifier": ident, "metadata": trim_metadata(cand.item), "probes": probes,
               "logs": logs, "aucdtect": aucdtect}
        (FIXTURES / f"{key}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
        print(f"{key:16} {ident[:50]:50} probes={len(probes)} logs={len(logs)} aucdtect={len(aucdtect)}")


if __name__ == "__main__":
    main()
