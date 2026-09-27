"""Orchestration: identifiers or queries in, verified and ranked candidates out."""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import altmedia, genre as genre_mod, integrity, matching, provenance as prov, queries, spectral
from .flac_probe import FlacProbe, cross_check, probe_header
from .genre import CONFIDENCE, GenreHit, GenreResult, GenreSpec
from .ia_client import IAClient, IAError
from .model import ItemInfo, as_list
from .queries import Want

LOG_MAX_BYTES = 300_000
TIER_RANK = {"A": 0, "B": 1, "C": 2, "L": 3, "D": 4, "?": 5}
WANTS_LIVE = re.compile(r"\b(live|concert|unplugged|bootleg|sbd|soundboard)\b", re.I)


@dataclass
class Options:
    top: int = 3                        # candidates listed per request
    include_live: bool = False
    free_only: bool = False
    rip_only: bool = False              # only the CD-rip-shaped rung (Log or Cue Sheet present)
    deep: str = "top"                   # none | top | all
    spectral: bool = True
    max_candidates: int = 10            # how many search hits get fully inspected
    max_deep_bytes: int = 1500 * 1024 * 1024
    spectrogram_dir: Optional[Path] = None
    fallback: tuple = ()                # when no FLAC is found, look for these instead: ("wav", "vbr")


@dataclass
class Candidate:
    item: ItemInfo
    via: str = ""                                            # which query found it
    probes: dict = field(default_factory=dict)               # file name -> FlacProbe
    stats: Optional[prov.ProbeStats] = None
    issues: list = field(default_factory=list)               # cross-check problems (IA vs header)
    logs: list = field(default_factory=list)                 # LogEvidence
    aucdtect: dict = field(default_factory=dict)
    provenance: Optional[prov.Provenance] = None
    match: Optional[matching.MatchResult] = None
    integrity: list = field(default_factory=list)            # [(file name, IntegrityResult)]
    spectral: object = None                                  # spectral.SpectralResult
    levels: list = field(default_factory=lambda: ["V0"])
    alternates: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    genre_conf: str = ""
    alt_stats: Optional[altmedia.AltStats] = None            # WAV / VBR MP3 candidates (no FLAC exists): header facts

    @property
    def tier(self) -> str:
        return self.provenance.tier if self.provenance else "?"

    @property
    def identifier(self) -> str:
        return self.item.identifier

    @property
    def artist_album(self) -> tuple:
        st = self.stats
        return matching.guess_artist_album(self.item.title, self.item.creator,
                                           st.tag_artist if st else "", st.tag_album if st else "")

    @property
    def album_likeness(self) -> float:
        return matching.album_likeness(len(self.item.flacs), self.item.runtime_seconds)

    def rank_key(self) -> tuple:
        m = self.match
        return (m.bucket if m else 2, TIER_RANK[self.tier], -round(m.score if m else 0, 1),
                -self.album_likeness, -(self.provenance.score if self.provenance else 0), -self.item.downloads)


@dataclass
class Hit:
    doc: dict
    via: str
    quick: matching.MatchResult
    rip_shaped: bool


@dataclass
class WantResult:
    want: Want
    rungs: list = field(default_factory=list)          # [(label, hits)]
    candidates: list = field(default_factory=list)     # ranked, one per album cluster
    matched: list = field(default_factory=list)        # every tested candidate that matched, ranked (duplicates included)
    near_misses: list = field(default_factory=list)    # [{identifier, title, creator, downloads}]
    lossless_other: list = field(default_factory=list)
    excluded: dict = field(default_factory=dict)       # reason -> count
    notes: list = field(default_factory=list)
    found: list = field(default_factory=list)          # search results (metadata only), listed before any testing
    untested: list = field(default_factory=list)       # found but not chosen for testing
    tested: bool = False
    fallback_done: bool = False


def first(value) -> str:
    items = as_list(value)
    return items[0] if items else ""


class Pipeline:
    def __init__(self, client: IAClient, log: Optional[Callable[[str], None]] = None,
                 max_probe_tracks: int = 10):
        self.client = client
        self.log = log or (lambda msg: None)
        self.max_probe_tracks = max_probe_tracks

    # ---- V0: metadata ---------------------------------------------------------------------

    def load(self, identifier: str, via: str = "", downloads: Optional[int] = None, media: str = "flac") -> Candidate:
        item = ItemInfo.from_metadata(self.client.metadata(identifier))
        item.media = media
        if downloads is not None:
            item.downloads = downloads
        return Candidate(item=item, via=via)

    # ---- V1: header probes ----------------------------------------------------------------

    def probe_alt(self, cand: Candidate) -> None:
        """V1 for WAV / VBR MP3 candidates: real headers, format, and (WAV) CD sector alignment."""
        media, files = cand.item.media, cand.item.flacs
        if len(files) > self.max_probe_tracks:
            step, total = len(files) / self.max_probe_tracks, len(files)
            files = [files[int(i * step)] for i in range(self.max_probe_tracks)]
            cand.notes.append(f"probed {len(files)} of {total} files (evenly sampled)")

        def one(f):
            return f.name, altmedia.probe_file(media, lambda start, n: self.client.range_get(cand.identifier, f.name, start, n),
                                               f.size, f.length)

        with ThreadPoolExecutor(self.client.max_concurrency) as pool:
            probes = dict(pool.map(one, files))
        cand.alt_stats = altmedia.build_stats(media, probes)
        if cand.alt_stats.ok and not cand.alt_stats.bad:
            cand.levels.append("V1")

    def probe(self, cand: Candidate) -> None:
        if cand.item.media != "flac":
            return self.probe_alt(cand)
        flacs = cand.item.flacs
        if len(flacs) > self.max_probe_tracks:
            step = len(flacs) / self.max_probe_tracks
            total = len(flacs)
            flacs = [flacs[int(i * step)] for i in range(self.max_probe_tracks)]
            cand.notes.append(f"probed {len(flacs)} of {total} FLAC files (evenly sampled)")

        def one(f):
            def fetch(start: int, length: int) -> bytes:
                return self.client.range_get(cand.identifier, f.name, start, length)
            try:
                return f.name, probe_header(fetch)
            except IAError as exc:
                return f.name, FlacProbe(kind="error", ok=False, detail=str(exc))

        with ThreadPoolExecutor(self.client.max_concurrency) as pool:
            cand.probes = dict(pool.map(one, flacs))
        cand.stats = prov.probe_stats(cand.probes)
        by_name = {f.name: f for f in flacs}
        cand.issues = [f"{name.rsplit('/', 1)[-1]}: {issue}" for name, p in cand.probes.items()
                       for issue in cross_check(p, by_name[name].size, by_name[name].length)]
        if cand.stats.n and not cand.stats.bad:
            cand.levels.append("V1")

    # ---- sidecars: rip logs, auCDtect reports ------------------------------------------------

    def read_sidecars(self, cand: Candidate, max_files: int = 4) -> None:
        item = cand.item
        logs = [f for f in item.files_of("log")[:max_files] if 0 < f.size <= LOG_MAX_BYTES]
        reports = [f for f in item.files_of("aucdtect")[:max_files] if 0 < f.size <= LOG_MAX_BYTES]

        def fetch(f):
            try:
                return f, self.client.get_bytes(item.identifier, f.name, LOG_MAX_BYTES), ""
            except IAError as exc:
                return f, b"", str(exc)

        with ThreadPoolExecutor(self.client.max_concurrency) as pool:
            fetched_logs, fetched_reports = list(pool.map(fetch, logs)), list(pool.map(fetch, reports))
        for f, raw, err in fetched_logs:
            if err:
                cand.notes.append(f"could not read {f.name}: {err}")
            else:
                cand.logs.append(prov.parse_rip_log(f.name, raw))
        totals = {"cdda": 0, "mpeg": 0, "mpeg_max": 0}
        for f, raw, err in fetched_reports:
            if not err:
                got = prov.parse_aucdtect(raw)
                totals["cdda"] += got["cdda"]
                totals["mpeg"] += got["mpeg"]
                totals["mpeg_max"] = max(totals["mpeg_max"], got["mpeg_max"])
        cand.aucdtect = totals

    # ---- verdict --------------------------------------------------------------------------

    def assess(self, cand: Candidate) -> None:
        if cand.item.media != "flac":
            cand.provenance = altmedia.assess(cand.item, cand.alt_stats or altmedia.AltStats(cand.item.media), cand.issues)
            return
        integrity_ok = all(r.ok for _, r in cand.integrity) if cand.integrity else None
        spectral_verdict = cand.spectral.verdict if cand.spectral else None
        cand.provenance = prov.assess(cand.item, cand.stats or prov.ProbeStats(), cand.logs, cand.aucdtect,
                                      integrity_ok=integrity_ok, spectral_verdict=spectral_verdict)

    def inspect(self, cand: Candidate, want: Optional[Want] = None) -> Candidate:
        """V0 + V1 + sidecars + tiering (+ match against the request) for one candidate."""
        self.probe(cand)
        self.read_sidecars(cand)
        self.assess(cand)
        if want is not None:
            st = cand.stats
            cand.match = matching.match_fields(want.artist, want.album, cand.item.title, cand.item.creator,
                                               st.tag_artist if st else "", st.tag_album if st else "")
        return cand

    def inspect_all(self, cands: list, label: str, want: Optional[Want] = None, workers: int = 3) -> None:
        """Inspect several candidates at once. The client still caps in-flight requests, so this only
        keeps that cap busy instead of idling between one candidate's phases and the next."""
        done = [0]

        def run(cand):
            self.inspect(cand, want)
            done[0] += 1
            self.log(f"  [{label}] inspected {done[0]}/{len(cands)}: {cand.identifier} "
                     f"({len(cand.item.flacs)} FLAC) -> tier {cand.tier}")

        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(run, cands))

    # ---- find -----------------------------------------------------------------------------

    @staticmethod
    def _quick_match(want: Want, doc: dict) -> matching.MatchResult:
        return matching.match_fields(want.artist, want.album, first(doc.get("title")), first(doc.get("creator")))

    def _search_pool(self, want: Want, opts: Options, res: WantResult) -> dict:
        rungs = (queries.album_rungs(want.artist, want.album, opts.rip_only) if want.album
                 else queries.artist_rungs(want.artist, opts.rip_only))
        pool: dict = {}
        for rung in rungs:
            # CD-rip-shaped and plain-FLAC (including vinyl rips) are always both searched, so a pile of CD rips can't hide
            # the rest; only the loosest word-matching rung is skipped once there are enough exact matches.
            if want.album and rung.label.startswith("relaxed") and sum(1 for h in pool.values() if h.quick.bucket == 0) >= 3:
                break
            docs = list(self.client.search_iter(rung.query, max_hits=rung.rows))
            res.rungs.append((rung.label, len(docs)))
            self.log(f"  [{want.label}] {rung.label}: {len(docs)} hit(s)")
            for d in docs:
                pool.setdefault(d["identifier"], Hit(d, rung.label, self._quick_match(want, d), "rip-shaped" in rung.label))
        return pool

    @staticmethod
    def choose_hits(plausible: list, limit: int, reserve: int = 3) -> list:
        """The best `limit` hits, but never letting CD-rip-shaped ones fill every slot: up to `reserve` places go to the
        best hits that are not (vinyl rips, plain FLAC uploads), so they are listed and can be chosen."""
        head = plausible[:limit]
        plain_in_head = [h for h in head if not h.rip_shaped]
        plain_rest = [h for h in plausible[limit:] if not h.rip_shaped]
        need = min(reserve, limit // 3, len(plain_in_head) + len(plain_rest)) - len(plain_in_head)
        if need > 0 and plain_rest:
            keep = [h for h in head if h.rip_shaped][: limit - need] + plain_in_head
            head = [h for h in plausible if h in keep or h in plain_rest[:need]]
        return head

    def search_one(self, want: Want, opts: Options) -> WantResult:
        """The search phase: queries plus item metadata. No file is opened; nothing is tested. The result's `found`
        list is what the user gets to look at (and choose from) before `verify_selected` does any testing."""
        res = WantResult(want=want)
        pool = self._search_pool(want, opts, res)
        ranked_docs = sorted(pool.values(), key=lambda h: (-round(h.quick.score, 1), not h.rip_shaped,
                                                           -int(h.doc.get("downloads") or 0)))
        plausible = [h for h in ranked_docs if h.quick.bucket < 2]
        res.near_misses = [{"identifier": h.doc["identifier"], "title": first(h.doc.get("title")),
                            "creator": first(h.doc.get("creator")), "downloads": int(h.doc.get("downloads") or 0)}
                           for h in ranked_docs if h.quick.bucket == 2][:8]
        selected = self.choose_hits(plausible, opts.max_candidates)
        self.log(f"  [{want.label}] reading the item records of {len(selected)} candidate(s) (about 3 s each when not cached)")

        def load_one(h):
            try:
                return self.load(h.doc["identifier"], via=h.via, downloads=int(h.doc.get("downloads") or 0))
            except IAError as exc:
                res.notes.append(f"could not load {h.doc['identifier']}: {exc}")
                return None

        with ThreadPoolExecutor(self.client.max_concurrency) as ex:
            loaded = [c for c in ex.map(load_one, selected) if c]

        self._admit(res, loaded, want, opts)
        if not res.found and opts.fallback and not opts.rip_only:
            self._alt_search(res, opts)
        if not res.found:
            self._fallback(res)
        return res

    def _admit(self, res: WantResult, loaded: list, want: Want, opts: Options) -> None:
        """Put loaded candidates on the list of what was found, or count why they were left out."""
        wants_live = bool(WANTS_LIVE.search(want.raw))
        for cand in loaded:
            reason = ""
            if not cand.item.flacs:
                reason = "no .flac files" if cand.item.media == "flac" else f"no {cand.item.media.upper()} files"
            elif cand.item.is_live and not (opts.include_live or wants_live):
                reason = "live recording (use --include-live)"
            elif opts.free_only and not cand.item.is_free:
                reason = "no free licence (--free-only)"
            elif not want.album and cand.album_likeness < 0.5:
                reason = "not album-shaped (single, or very short)"
            if reason:
                res.excluded[reason] = res.excluded.get(reason, 0) + 1
            else:
                cand.match = matching.match_fields(want.artist, want.album, cand.item.title, cand.item.creator)
                res.found.append(cand)

    def _alt_search(self, res: WantResult, opts: Options) -> None:
        """No FLAC matched: search again for WAV (lossless) and VBR MP3 uploads, WAV listed first."""
        want = res.want
        names = " / ".join(queries.ALT_NAME[m] for m in opts.fallback)
        self.log(f"  [{want.label}] no FLAC found; searching for {names} instead")
        for media in opts.fallback:
            pool: dict = {}
            for rung in queries.alt_rungs(want.artist, want.album, media):
                docs = list(self.client.search_iter(rung.query, max_hits=rung.rows))
                res.rungs.append((rung.label, len(docs)))
                self.log(f"  [{want.label}] {rung.label}: {len(docs)} hit(s)")
                for d in docs:
                    pool.setdefault(d["identifier"], Hit(d, rung.label, self._quick_match(want, d), False))
                if any(h.quick.bucket == 0 for h in pool.values()):
                    break
            hits = sorted((h for h in pool.values() if h.quick.bucket < 2),
                          key=lambda h: (-round(h.quick.score, 1), -int(h.doc.get("downloads") or 0)))[: opts.max_candidates]

            already = {c.identifier for c in res.found}          # a WAV upload also carries IA's derived VBR MP3s: list it once

            def load_one(h, media=queries.ALT_MEDIA[media]):
                if h.doc["identifier"] in already:
                    return None
                try:
                    return self.load(h.doc["identifier"], via=h.via, downloads=int(h.doc.get("downloads") or 0), media=media)
                except IAError as exc:
                    res.notes.append(f"could not load {h.doc['identifier']}: {exc}")
                    return None

            with ThreadPoolExecutor(self.client.max_concurrency) as ex:
                self._admit(res, [c for c in ex.map(load_one, hits) if c], want, opts)
        if res.found:
            res.notes.append("no FLAC found for this request: these are " + names + " uploads instead. "
                             "WAV is lossless and is converted to FLAC when saved; VBR MP3 is lossy and stays MP3")

    def _fallback(self, res: WantResult) -> None:
        """Nothing matched: say so, and show what does exist (the artist's other FLAC items, other lossless formats)."""
        want = res.want
        res.fallback_done = True
        res.notes.append("no FLAC candidate matched this request")
        if not res.near_misses:
            near = queries.near_miss_rung(want.artist)
            res.near_misses = [{"identifier": d["identifier"], "title": first(d.get("title")),
                                "creator": first(d.get("creator")), "downloads": int(d.get("downloads") or 0)}
                               for d in self.client.search_iter(near.query, max_hits=near.rows)][:8]
        other = queries.other_lossless_rung(want.artist, want.album)
        res.lossless_other = [{"identifier": d["identifier"], "title": first(d.get("title")),
                               "creator": first(d.get("creator"))}
                              for d in self.client.search_iter(other.query, max_hits=other.rows)][:5]

    def verify_selected(self, res: WantResult, selected: list, opts: Options) -> WantResult:
        """The test phase, for the candidates the user picked from `res.found`: header reads and rip logs, tiering,
        ranking and duplicate grouping, then (per opts.deep) the full decode and spectral checks of the best pick."""
        want = res.want
        chosen = {id(c) for c in selected}
        res.untested = [c for c in res.found if id(c) not in chosen]
        res.tested = bool(selected)
        self.inspect_all(selected, want.label, want)
        for cand in [c for c in selected if c.match.bucket == 2]:
            res.near_misses.insert(0, {"identifier": cand.identifier, "title": cand.item.title,
                                       "creator": cand.item.creator, "downloads": cand.item.downloads})
        matches = [c for c in selected if c.match.bucket < 2]
        res.matched = sorted(matches, key=Candidate.rank_key)
        res.candidates = self.cluster(res.matched)
        if not res.candidates and selected and not res.fallback_done:
            self._fallback(res)                       # what was chosen did not hold up once its tags were read
        if opts.deep != "none" and res.candidates:
            self.deep_stage(res, opts)
        res.candidates = res.candidates[: max(opts.top, 1)] if opts.deep != "all" else res.candidates
        return res

    def find_one(self, want: Want, opts: Options) -> WantResult:
        """Search, then test everything that was found (the behaviour before search and test were separated)."""
        res = self.search_one(want, opts)
        return self.verify_selected(res, list(res.found), opts)

    @staticmethod
    def cluster(ranked: list) -> list:
        """One entry per album: duplicate uploads become `alternates` of the best copy."""
        primaries: dict = {}
        order: list = []
        for cand in ranked:
            key = matching.cluster_key(*cand.artist_album)
            if key in primaries:
                primaries[key].alternates.append(
                    {"identifier": cand.identifier, "tier": cand.tier, "format": cand.stats.format_label() if cand.stats else "",
                     "flac_files": len(cand.item.flacs), "mb": round(cand.item.flac_bytes / 1e6),
                     "downloads": cand.item.downloads})
            else:
                primaries[key] = cand
                order.append(cand)
        return order

    # ---- V2 + V3 --------------------------------------------------------------------------

    def deep_verify(self, cand: Candidate, opts: Options, integrity_check: bool = True,
                    spectral_check: bool = True) -> None:
        """V2: stream every file through `flac -t` (nothing is stored). V3: spectral check on 3 slices."""
        if cand.item.media != "flac":
            cand.notes.append("no decode or spectrum test for WAV / MP3: the download is checked against archive.org's checksums"
                              + (" and every WAV is verified as it is encoded to FLAC" if cand.item.media == "wav" else ""))
            self.assess(cand)
            return
        item = cand.item
        flacs = item.flacs
        if integrity_check:
            total = sum(f.size for f in flacs)
            if total > opts.max_deep_bytes:
                cand.notes.append(f"full decode test skipped: {total / 1e6:,.0f} MB is over the "
                                  f"{opts.max_deep_bytes // 2**20} MB limit (--max-deep-mb)")
            else:
                results = []
                for i, f in enumerate(flacs, 1):
                    self.log(f"    [{cand.identifier}] decode test {i}/{len(flacs)}: "
                             f"{f.name.rsplit('/', 1)[-1]} ({f.size / 1e6:.0f} MB)")
                    probe = cand.probes.get(f.name)
                    md5_set = probe.streaminfo.md5_set if probe and probe.streaminfo else None
                    result = integrity.test_file(self.client, cand.identifier, f, md5_set)
                    results.append((f.name, result))
                    if not result.ok:
                        cand.notes.append(f"decode testing stopped at the first failure: {f.name.rsplit('/', 1)[-1]}")
                        break
                cand.integrity = results
                if len(results) == len(flacs) and all(r.ok for _, r in results):
                    cand.levels.append("V2")
        if spectral_check:
            png = opts.spectrogram_dir / f"{cand.identifier}.png" if opts.spectrogram_dir else None
            try:
                cand.spectral = spectral.analyze_item(self.client, item, cand.probes, png, log=self.log)
            except IAError as exc:
                cand.notes.append(f"spectral check failed: {exc}")
            else:
                if cand.spectral.verdict in ("PASS", "SUSPECT", "LIKELY_LOSSY", "UPSAMPLED"):
                    cand.levels.append("V3")
        self.assess(cand)

    def deep_candidates(self, cands: list, opts: Options, label: str) -> None:
        """Deep-check the top pick; if it fails outright, move on to the next (up to three). deep=all checks every one."""
        for cand in (cands if opts.deep == "all" else cands[:3]):
            self.log(f"  [{label}] deep check: {cand.identifier}")
            self.deep_verify(cand, opts, integrity_check=True, spectral_check=opts.spectral)
            if opts.deep == "top" and cand.tier != "D":
                break

    def deep_stage(self, res: WantResult, opts: Options) -> None:
        self.deep_candidates(res.candidates, opts, res.want.label)
        res.candidates.sort(key=Candidate.rank_key)

    def find(self, wants: list, opts: Options) -> list:
        return [self.find_one(w, opts) for w in wants]

    # ---- genre ----------------------------------------------------------------------------

    @staticmethod
    def genre_key(cand: Candidate) -> tuple:
        pull = (CONFIDENCE.get(cand.genre_conf, 1.0) + cand.album_likeness
                + 0.4 * math.log10(1 + cand.item.downloads) + (cand.provenance.score if cand.provenance else 0) / 100)
        return (TIER_RANK[cand.tier], -pull)

    @staticmethod
    def prefilter(doc: dict, min_tracks: int, include_live: bool) -> str:
        """Reasons to drop a search hit using only what the search already returned (no metadata call)."""
        stub = ItemInfo(identifier=doc.get("identifier", ""), title=first(doc.get("title")),
                        collections=as_list(doc.get("collection")),
                        extra={k: first(doc.get(k)) for k in ("venue", "taper") if doc.get(k)})
        if stub.is_live and not include_live:
            return "live recording (use --include-live)"
        size, files = int(doc.get("item_size") or 0), int(doc.get("files_count") or 0)
        if 0 < size < 30_000_000:
            return "not album-shaped (very short or a huge dump)"
        if 0 < files < min_tracks:
            return f"fewer than {min_tracks} FLAC files (single or EP; see --min-tracks)"
        return ""

    def genre_search(self, spec: GenreSpec, opts: Options) -> GenreResult:
        """The search phase: queries plus item metadata. Nothing is tested; `found` is what the user gets to look at."""
        res = GenreResult(spec=spec)
        pool: dict = {}

        def run(label: str, query: str, rip_shaped: bool, default_conf: str, max_hits: int) -> None:
            docs = list(self.client.search_iter(query, max_hits=max_hits))
            res.searched.append((label, len(docs), query))
            self.log(f"  [{spec.genre}] {label}: {len(docs)} hit(s)")
            for d in docs:
                pool.setdefault(d["identifier"], GenreHit(d, label, rip_shaped, genre_mod.confidence(d, spec.terms, default_conf)))

        kw = dict(years=spec.years, exclude=tuple(spec.exclude), collections=tuple(spec.collections))
        tag = ", CD-rip-shaped" if spec.cd_only else ""
        run(f"subject/title match{tag}", queries.genre_query(spec.terms, fields=("subject", "title"), rip_shaped=spec.cd_only, **kw),
            spec.cd_only, "title", 300)
        if len(pool) < spec.enrich_n:
            run(f"description match{tag}", queries.genre_query(spec.terms, fields=("description",), rip_shaped=spec.cd_only, **kw),
                spec.cd_only, "description", 150)
        if spec.cd_only and len(pool) < spec.top * 2:
            res.notes.append(f"only {len(pool)} CD-rip-shaped hits, so the search was widened to any FLAC "
                             "(those items have no rip log or cue sheet, so expect lower tiers)")
            run("subject/title match, any FLAC", queries.genre_query(spec.terms, fields=("subject", "title"), rip_shaped=False, **kw),
                False, "title", 200)

        hits = []
        for h in sorted(pool.values(), key=lambda h: -h.pre_score()):
            reason = self.prefilter(h.doc, spec.min_tracks, spec.include_live or opts.include_live)
            if reason:
                res.excluded[reason] = res.excluded.get(reason, 0) + 1
            elif len(hits) < spec.enrich_n:
                hits.append(h)
        self.log(f"  [{spec.genre}] reading the item records of {len(hits)} candidate(s) (about 3 s each when not cached)")

        def load_one(h):
            try:
                return h, self.load(h.doc["identifier"], via=h.via, downloads=int(h.doc.get("downloads") or 0))
            except IAError as exc:
                res.notes.append(f"could not load {h.doc['identifier']}: {exc}")
                return h, None

        with ThreadPoolExecutor(self.client.max_concurrency) as ex:
            loaded = [pair for pair in ex.map(load_one, hits) if pair[1]]
        kept = []
        for h, cand in loaded:
            item = cand.item
            single_image = (len(item.flacs) == 1 and (item.runtime_seconds or 0) >= 25 * 60 and item.files_of("cue"))
            reason = ""
            if not item.flacs:
                reason = "no .flac files"
            elif item.is_live and not (spec.include_live or opts.include_live):
                reason = "live recording (use --include-live)"
            elif spec.free_only and not item.is_free:
                reason = "no free licence (--free-only)"
            elif len(item.flacs) < spec.min_tracks and not single_image:
                reason = f"fewer than {spec.min_tracks} FLAC files (single or EP; see --min-tracks)"
            elif cand.album_likeness < 0.5:
                reason = "not album-shaped (very short or a huge dump)"
            if reason:
                res.excluded[reason] = res.excluded.get(reason, 0) + 1
                continue
            cand.genre_conf = h.conf
            tags = genre_mod.matched_subjects(item.subjects, spec.terms)
            cand.notes.append(f"genre match via {h.conf}" + (f" ({', '.join(tags)})" if tags else ""))
            kept.append(cand)
        res.found = kept
        return res

    def genre_inspect(self, res: GenreResult, selected: list, opts: Options) -> GenreResult:
        """The test phase, for the candidates the user picked from `res.found`."""
        spec = res.spec
        chosen = {id(c) for c in selected}
        res.untested = [c for c in res.found if id(c) not in chosen]
        res.tested = bool(selected)
        if res.untested:
            res.notes.append(f"{len(res.untested)} more candidate(s) were listed but not inspected (see --probe / --select)")
        self.inspect_all(selected, spec.genre)
        ranked = genre_mod.diversify(self.cluster(sorted(selected, key=self.genre_key)), spec.per_artist)[: spec.top]
        if opts.deep != "none" and ranked:
            self.deep_candidates(ranked, opts, spec.genre)
            ranked.sort(key=self.genre_key)
        res.candidates = ranked
        return res

    def genre(self, spec: GenreSpec, opts: Options) -> GenreResult:
        """Search, then inspect the best `spec.probe_n` found (the behaviour before search and test were separated)."""
        res = self.genre_search(spec, opts)
        return self.genre_inspect(res, res.found[: spec.probe_n], opts)
