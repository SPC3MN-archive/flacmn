"""Turn raw evidence (header probes, rip logs, sidecar files, tags, descriptions) into a tier.

A tier says how sure we are that the audio is genuinely lossless, whatever its source. The goal is any
high-quality audio: a CD rip, a lossless or hi-res download, a vinyl or tape transfer are all equally welcome, and no
source or format is ranked above another. Tiers, best first:
  A  genuine lossless, strong evidence  (a verified rip log with aligned tracks; or a clean spectrum and a passed
                                         decode test with corroboration, such as real content above 22 kHz)
  B  genuine lossless, likely           (some evidence: a clean spectrum, aligned tracks, a log we could not confirm)
  C  lossless, little evidence          (real FLAC, but nothing yet speaks for or against it)
  D  failed or suspect                  (not FLAC, integrity failure, lossy-codec spectrum, "fake" claims)

Evidence of a clean CD extraction (rip logs, AccurateRip, sector-aligned tracks) still counts, as evidence; it is
not a preference for CDs. Everything here is evidence, not proof: logs and descriptions are uploader-supplied. The
weights are named constants so they can be tuned in one place; tests pin real-world calibration items.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .flac_probe import CUESHEET
from .model import ItemInfo

WEIGHTS = dict(
    log_verified=40, log_clean=25, log_present=15, log_unrecognised=5, log_errors=-25,
    accurip_file=8, cue_file=12, checksum_file=2, aucdtect_cdda=10,
    aligned_strong=25, aligned_weak=8,
    ripper_tags=10, cuesheet_block=5, media_tag_cd=4, claims_source=6, claim_contradicted=-10,
    claims_fake=-20, live=-10, spectral_suspect=-15, upsampled=-5,
    spectral_pass=30, decode_pass=15, hires_content=20,
)
A_MIN, B_MIN = 70, 22
ALIGNED_MIN_TRACKS = 3      # alignment is only convincing in aggregate
ALIGNED_MIN_RATIO = 0.9
AUCDTECT_MPEG_FAIL = 80     # % confidence that a track is MPEG-derived

TIER_LABELS = {
    "A": "Genuine lossless, strong evidence",
    "B": "Genuine lossless, likely",
    "C": "Lossless, source not verified as CD",
    "L": "Lossy MP3 (no FLAC or WAV exists)",
    "D": "Failed or suspect",
}

RIPPERS = [("EAC", r"exact audio copy"), ("XLD", r"x lossless decoder"), ("dBpoweramp", r"dbpoweramp"),
           ("whipper", r"whipper|morituri"), ("CUERipper", r"cueripper"), ("cdparanoia", r"cdparanoia")]
_ACCURATE = re.compile(r"accurately ripped|rip accurate|(?<!not )(?<!verified as )\baccurate \(confidence", re.I)
_XLD_OK = re.compile(r"track\s*\d+\s*:\s*ok\s*\(\s*v\d", re.I)
_ALL_ACCURATE = re.compile(r"all tracks accurately ripped", re.I)
_NO_ERRORS = re.compile(r"no errors occurred", re.I)
_QUALITY = re.compile(r"track quality\s*[:=]?\s*([\d.]+)\s*%", re.I)
_ERROR_TEXT = re.compile(r"there were errors|suspicious position|timing problem|unrecoverable", re.I)
_ERROR_COUNTS = re.compile(r"(read error|skipped \(treated as error\)|damaged sector count)\s*:\s*(\d+)", re.I)
_NOT_IN_DB = re.compile(r"not present in accuraterip database|not found in (the )?accuraterip", re.I)
_AUCDTECT = re.compile(r"(?:looks like\s+)?\b(CDDA|MPEG)\b(?:\s+with probability)?\s*[:\-]?\s*(\d{1,3})\s*%", re.I)

CD_CLAIM = re.compile(
    r"\b(exact audio copy|eac|xld|x lossless decoder|dbpoweramp|accuraterip|cuetools|cdparanoia|whipper|"
    r"cd[\s-]?rip(ped)?|ripped (from|off) (the |a |my )?cd|cdda|secure mode)\b", re.I)
OTHER_SOURCE = {
    "vinyl": re.compile(r"\b(vinyl|lp rip|needle ?drop|turntable|33 ?rpm|45 ?rpm|78 ?rpm)\b", re.I),
    "tape": re.compile(r"\b(cassette|reel[\s-]to[\s-]reel|tape (rip|transfer))\b", re.I),
    "web/store": re.compile(r"\b(bandcamp|qobuz|hdtracks|itunes (plus|store|match)|amazon (music|mp3|digital)|"
                            r"tidal|beatport|web (release|rip)|digital (release|download)|studio master)\b", re.I),
}
FAKE_CLAIM = re.compile(r"\b(upsampl\w*|transcode\w*|fake flacs?|from (an? )?mp3s?|converted from mp3s?|"
                        r"lossy source)\b", re.I)
NEGATION = re.compile(r"\b(no|not|never|without|non|zero|free of|isn'?t|aren'?t)\b")
RIPPER_TAG_WORDS = re.compile(r"\b(eac|exact audio copy|xld|x lossless decoder|dbpoweramp|cuetools|cueripper|"
                              r"whipper|cdparanoia)\b", re.I)
RIPPER_TAG_KEYS = ("ACCURATERIPRESULT", "ACCURATERIPDISCID", "ACCURATERIPCOUNT", "DISCID",
                   "MUSICBRAINZ_DISCID", "CDDB")
RIPPER_TEXT_KEYS = ("COMMENT", "DESCRIPTION", "ENCODER", "ENCODEDBY", "ENCODED-BY", "RIPPER", "SOURCE")


# ---- rip logs and auCDtect reports ----------------------------------------------------------

def decode_text(raw: bytes) -> str:
    """EAC logs are usually UTF-16 with a BOM; others are UTF-8 or cp1252."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    if raw[:200].count(b"\x00") > 20:
        return raw.decode("utf-16-le", "replace")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


@dataclass
class LogEvidence:
    file: str
    ripper: str = ""
    accurate_tracks: int = 0
    all_accurate: bool = False
    no_errors: bool = False
    has_errors: bool = False
    copy_ok: int = 0
    min_quality: Optional[float] = None
    not_in_db: bool = False

    @property
    def strength(self) -> str:
        if not self.ripper:
            return "unrecognised"
        if self.all_accurate or (self.accurate_tracks and not self.has_errors):
            return "verified"
        if self.has_errors:
            return "errors"
        if self.no_errors or self.copy_ok or (self.min_quality is not None and self.min_quality >= 99.9):
            return "clean"
        return "present"

    def summary(self) -> str:
        if not self.ripper:
            return f"{self.file.rsplit('/', 1)[-1]}: log of unknown origin"
        bits = [self.ripper]
        if self.all_accurate:
            bits.append("all tracks accurately ripped")
        elif self.accurate_tracks:
            bits.append(f"{self.accurate_tracks} track(s) accurately ripped")
        elif self.not_in_db:
            bits.append("not in AccurateRip database")
        if self.has_errors and not self.all_accurate:
            bits.append("errors reported")
        elif self.no_errors:
            bits.append("no errors")
        return ", ".join(bits)


def parse_rip_log(name: str, raw: bytes) -> LogEvidence:
    text = decode_text(raw)
    ev = LogEvidence(file=name)
    ev.ripper = next((label for label, pat in RIPPERS if re.search(pat, text, re.I)), "")
    ev.accurate_tracks = len(_ACCURATE.findall(text)) + len(_XLD_OK.findall(text))
    ev.all_accurate = bool(_ALL_ACCURATE.search(text))
    ev.no_errors = bool(_NO_ERRORS.search(text))
    ev.copy_ok = len(re.findall(r"\bcopy ok\b", text, re.I))
    qualities = [float(q) for q in _QUALITY.findall(text) if re.fullmatch(r"\d+(\.\d+)?", q)]
    ev.min_quality = min(qualities) if qualities else None
    ev.not_in_db = bool(_NOT_IN_DB.search(text))
    ev.has_errors = bool(_ERROR_TEXT.search(text)) or any(int(n) > 0 for _, n in _ERROR_COUNTS.findall(text))
    if ev.no_errors and not _ERROR_TEXT.search(text):
        ev.has_errors = False
    return ev


def parse_aucdtect(raw: bytes) -> dict:
    """auCDtect labels each track CDDA or MPEG with a confidence. Returns per-label counts."""
    counts = {"cdda": 0, "mpeg": 0, "mpeg_max": 0}
    for label, pct in _AUCDTECT.findall(decode_text(raw)):
        pct = int(pct)
        if pct > 100:
            continue
        if label.upper() == "CDDA":
            counts["cdda"] += 1
        else:
            counts["mpeg"] += 1
            counts["mpeg_max"] = max(counts["mpeg_max"], pct)
    return counts


# ---- probe statistics -----------------------------------------------------------------------

@dataclass
class ProbeStats:
    n: int = 0                                   # files probed
    ok: int = 0                                  # real FLAC
    bad: list = field(default_factory=list)      # [(name, kind, detail)]
    aligned: int = 0
    cd_native: int = 0
    rates: Counter = field(default_factory=Counter)
    depths: Counter = field(default_factory=Counter)
    channels: Counter = field(default_factory=Counter)
    vendors: Counter = field(default_factory=Counter)
    tag_artist: str = ""
    tag_album: str = ""
    md5_unset: int = 0
    ripper_tags: list = field(default_factory=list)
    has_cuesheet_block: bool = False
    media_cd: bool = False
    warnings: list = field(default_factory=list)

    @property
    def aligned_ratio(self) -> float:
        return self.aligned / self.ok if self.ok else 0.0

    def format_label(self) -> str:
        if not self.ok:
            return "n/a"
        rate = self.rates.most_common(1)[0][0]
        depth = self.depths.most_common(1)[0][0]
        chans = self.channels.most_common(1)[0][0]
        label = f"{rate / 1000:g} kHz/{depth}-bit/{'stereo' if chans == 2 else f'{chans}ch'}"
        return label + (" (mixed)" if len(self.rates) > 1 or len(self.depths) > 1 else "")


def probe_stats(probes: dict) -> ProbeStats:
    st = ProbeStats(n=len(probes))
    artists, albums = Counter(), Counter()
    for name, p in probes.items():
        if not p.ok or not p.streaminfo:
            st.bad.append((name, p.kind, p.detail))
            continue
        si = p.streaminfo
        st.ok += 1
        st.aligned += si.sector_aligned
        st.cd_native += si.cd_native
        st.rates[si.sample_rate] += 1
        st.depths[si.bits_per_sample] += 1
        st.channels[si.channels] += 1
        st.md5_unset += not si.md5_set
        if p.vendor:
            st.vendors[p.vendor] += 1
        st.has_cuesheet_block |= p.has_block(CUESHEET)
        for key in ("ARTIST", "ALBUMARTIST"):
            if p.tags.get(key):
                artists[p.tags[key]] += 1
                break
        if p.tags.get("ALBUM"):
            albums[p.tags["ALBUM"]] += 1
        for key in RIPPER_TAG_KEYS:
            if p.tags.get(key) and f"tag {key}" not in st.ripper_tags:
                st.ripper_tags.append(f"tag {key}")
        for key in RIPPER_TEXT_KEYS:
            m = RIPPER_TAG_WORDS.search(p.tags.get(key, ""))
            if m and f"{key.lower()}: {m.group(0)}" not in st.ripper_tags:
                st.ripper_tags.append(f"{key.lower()}: {m.group(0)}")
        if re.search(r"cuetools|cueripper", p.vendor, re.I) and f"vendor {p.vendor}" not in st.ripper_tags:
            st.ripper_tags.append(f"vendor {p.vendor}")
        media = (p.tags.get("SOURCEMEDIA") or p.tags.get("MEDIA") or "").upper()
        st.media_cd |= media.startswith("CD")
        for w in p.warnings:
            if w not in st.warnings:
                st.warnings.append(w)
    st.tag_artist = artists.most_common(1)[0][0] if artists else ""
    st.tag_album = albums.most_common(1)[0][0] if albums else ""
    return st


# ---- claims in the uploader's text ------------------------------------------------------------

def scan_claims(item: ItemInfo) -> dict:
    text = " ".join([item.title, item.description, " ".join(item.subjects),
                     item.extra.get("source", ""), item.extra.get("notes", "")])
    cd = sorted({m.group(0).lower() for m in CD_CLAIM.finditer(text)})
    other = sorted(label for label, pat in OTHER_SOURCE.items() if pat.search(text))
    fake = []
    for m in FAKE_CLAIM.finditer(text):
        before = text[max(0, m.start() - 24):m.start()].lower()
        if not NEGATION.search(before):
            fake.append(m.group(0).lower())
    return {"cd": cd, "other": other, "fake": sorted(set(fake))}


# ---- the verdict ----------------------------------------------------------------------------

@dataclass
class Provenance:
    tier: str
    score: int
    reasons: list = field(default_factory=list)     # "✓ ..." evidence for, "✗ ..." against, "• ..." notes
    hard_fail: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return TIER_LABELS[self.tier]


def assess(item: ItemInfo, stats: ProbeStats, logs: list, aucdtect: dict, *,
           integrity_ok: Optional[bool] = None, spectral_verdict: Optional[str] = None) -> Provenance:
    w, reasons, hard, score = WEIGHTS, [], [], 0

    def add(points: int, text: str) -> None:
        nonlocal score
        score += points
        reasons.append(("✓ " if points > 0 else "✗ " if points < 0 else "• ") + text)

    if stats.bad:
        kinds = ", ".join(sorted({k for _, k, _ in stats.bad}))
        hard.append(f"{len(stats.bad)} of {stats.n} probed .flac files are not real FLAC ({kinds})")
    if integrity_ok is False:
        hard.append("full decode test (flac -t) or checksum failed")
    if spectral_verdict == "LIKELY_LOSSY":
        hard.append("spectrum shows a lossy-codec lowpass (probably transcoded from MP3/AAC)")
    if aucdtect.get("mpeg_max", 0) >= AUCDTECT_MPEG_FAIL:
        hard.append(f"the uploader's auCDtect report flags MPEG-derived tracks ({aucdtect['mpeg_max']}%)")

    # rip logs
    if logs:
        recognised = [lg for lg in logs if lg.ripper]
        strengths = {lg.strength for lg in recognised}
        if "errors" in strengths:
            add(w["log_errors"], "rip log reports errors: " + "; ".join(lg.summary() for lg in recognised))
        elif "verified" in strengths:
            add(w["log_verified"], "rip log: " + "; ".join(lg.summary() for lg in recognised if lg.strength == "verified"))
        elif "clean" in strengths:
            add(w["log_clean"], "rip log clean but not AccurateRip-verified: " + "; ".join(lg.summary() for lg in recognised))
        elif recognised:
            add(w["log_present"], "rip log present (" + recognised[0].ripper + ")")
        else:
            add(w["log_unrecognised"], f"{len(logs)} .log file(s) of unknown origin")
    for kind, key, text in (("cue", "cue_file", "cue sheet"), ("accurip", "accurip_file", ".accurip file"),
                            ("sfv", "checksum_file", ".sfv checksums"), ("md5", "checksum_file", "md5 checksums")):
        if item.files_of(kind):
            if not (kind == "md5" and item.files_of("sfv")):
                add(w[key], text)
    if aucdtect.get("cdda") and not aucdtect.get("mpeg"):
        add(w["aucdtect_cdda"], f"auCDtect report: {aucdtect['cdda']} track(s) CDDA, none MPEG (uploader-supplied)")

    # what the FLAC headers say
    if stats.ok:
        if stats.ok >= ALIGNED_MIN_TRACKS and stats.aligned_ratio >= ALIGNED_MIN_RATIO:
            add(w["aligned_strong"], f"{stats.aligned}/{stats.ok} tracks are a whole number of CD sectors (588 samples)")
        elif stats.aligned == stats.ok and stats.ok >= 1 and stats.ok < ALIGNED_MIN_TRACKS:
            add(w["aligned_weak"], f"{stats.aligned}/{stats.ok} track(s) sector-aligned (too few to be conclusive)")
        elif stats.ok >= ALIGNED_MIN_TRACKS and stats.aligned_ratio < 0.5:
            add(0, f"{stats.aligned}/{stats.ok} tracks sector-aligned (not a straight CD extraction, which is fine)")
        add(0, f"format: {stats.format_label()}")                # no format is preferred over another
        if stats.ripper_tags:
            add(w["ripper_tags"], "ripper fingerprints in the files: " + ", ".join(stats.ripper_tags[:3]))
        if stats.has_cuesheet_block:
            add(w["cuesheet_block"], "embedded CUESHEET block")
        if stats.media_cd:
            add(w["media_tag_cd"], "media tag says CD")
        if stats.md5_unset:
            add(0, f"{stats.md5_unset} file(s) have no STREAMINFO MD5, so audio integrity can only be checked by frame CRC")
        vendor = stats.vendors.most_common(1)[0][0] if stats.vendors else ""
        if vendor:
            add(0, f"encoder: {vendor}")

    # what the uploader says
    claims = scan_claims(item)
    sources = claims["cd"][:3] + claims["other"]
    if sources:
        add(w["claims_source"], "description names its source: " + ", ".join(sources))
    if claims["cd"]:
        contradiction = ""
        if stats.ok and stats.cd_native < stats.ok:
            contradiction = f"the files are {stats.format_label()}, not the CD format"
        elif stats.ok >= ALIGNED_MIN_TRACKS and stats.aligned_ratio < 0.5:
            contradiction = f"only {stats.aligned}/{stats.ok} tracks are sector-aligned"
        if contradiction:
            add(w["claim_contradicted"], f"description says {', '.join(claims['cd'][:2])}, but {contradiction}")
    if claims["fake"]:
        add(w["claims_fake"], "description mentions " + ", ".join(claims["fake"]))
    if item.is_live:
        add(w["live"], "looks like a live recording (venue/taper metadata or a dated title)")

    if spectral_verdict == "SUSPECT":
        add(w["spectral_suspect"], "spectrum has a sharp cutoff worth a look (see spectrogram)")
    elif spectral_verdict == "UPSAMPLED":
        add(w["upsampled"], "spectrum ends near 22 kHz although the file claims a higher sample rate")
    elif spectral_verdict == "PASS":
        add(w["spectral_pass"], "spectrum shows no lossy-codec lowpass")
        if stats.ok and stats.cd_native < stats.ok:
            add(w["hires_content"], "real content above 22 kHz: genuine hi-res, not upsampled")
    if integrity_ok:
        add(w["decode_pass"], "full decode test passed")

    if hard:
        tier = "D"
        reasons = [f"✗ {h}" for h in hard] + reasons
    else:
        tier = "A" if score >= A_MIN else "B" if score >= B_MIN else "C"
    return Provenance(tier=tier, score=score, reasons=reasons, hard_fail=hard)
