"""Rebuild ItemInfo / FlacProbe / log evidence from tests/fixtures/*.json (see record_fixtures.py)."""

from __future__ import annotations

import json
from pathlib import Path

from iaflac import provenance as prov
from iaflac.flac_probe import FlacProbe, StreamInfo
from iaflac.model import ItemInfo

FIXTURES = Path(__file__).parent / "fixtures"


def load(key: str):
    d = json.loads((FIXTURES / f"{key}.json").read_text())
    item = ItemInfo.from_metadata(d["metadata"])
    probes = {}
    for name, p in d["probes"].items():
        si = StreamInfo(**p["streaminfo"]) if p["streaminfo"] else None
        probes[name] = FlacProbe(kind=p["kind"], ok=p["ok"], detail=p["detail"], id3_prefix=p["id3_prefix"],
                                 streaminfo=si, blocks=[tuple(b) for b in p["blocks"]],
                                 audio_offset=p["audio_offset"], vendor=p["vendor"], tags=p["tags"],
                                 warnings=p["warnings"])
    logs = [prov.parse_rip_log(name, text.encode("utf-8")) for name, text in d["logs"].items()]
    aucdtect = {"cdda": 0, "mpeg": 0, "mpeg_max": 0}
    for text in d["aucdtect"].values():
        got = prov.parse_aucdtect(text.encode("utf-8"))
        aucdtect["cdda"] += got["cdda"]
        aucdtect["mpeg"] += got["mpeg"]
        aucdtect["mpeg_max"] = max(aucdtect["mpeg_max"], got["mpeg_max"])
    return item, probes, logs, aucdtect
