"""
Python port of the "8-dial, 4-bank" bassline interpreter designed to replace
esp-idf-link's 8-genre lookup-table bass engine.

This mirrors (in spirit and in the DP-solver / groove-template / contour
machinery) the SUBSTRATE HTML bassline interpreter, but reshaped around a
*hardware-realistic* control surface: 4 physical touch-pad "banks" x the
project's existing 2 physical potentiometers = 8 continuous parameters
total, instead of SUBSTRATE's ~50 sliders. Every design decision here
(which knobs get folded together, which are dropped) is deliberately made
to be portable 1:1 into embedded C++ (see esp-idf-link/main/bassline_interpreter.*).

Dial groups (bank, dial0, dial1):
  HARMONY : gravity   (chord-tone pull)      , color     (scale select + tension)
  GROOVE  : energy    (template + density)   , swing     (syncopation + swing ratio)
  MOTION  : contour   (pitch shape select)   , variation (leap/randomness/repeat)
  VOICE   : sweep     (filter automation)    , artic     (accent/ghost/slide energy)
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Tuple

# ------------------------------------------------------------------
# 1. Seeded RNG (mulberry32 port -- deterministic, matches SUBSTRATE's
#    approach so experiment runs are reproducible run-to-run; the actual
#    ESP32 firmware uses the chip's hardware RNG instead, since the
#    existing bass_engine.cpp already does and reproducibility isn't a
#    firmware requirement there).
# ------------------------------------------------------------------
class Mulberry32:
    def __init__(self, seed: int):
        self.state = seed & 0xFFFFFFFF

    def next(self) -> float:
        self.state = (self.state + 0x6D2B79F5) & 0xFFFFFFFF
        t = self.state
        t = (t ^ (t >> 15)) * (t | 1) & 0xFFFFFFFF
        t = (t + ((t ^ (t >> 7)) * (t | 61) & 0xFFFFFFFF)) ^ t
        t &= 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    def rc(self, prob: float) -> bool:
        return self.next() < prob

    def ri(self, n: int) -> int:
        if n <= 1:
            return 0
        return int(self.next() * n)

    def gumbel(self) -> float:
        u = min(max(self.next(), 1e-9), 1 - 1e-9)
        return -math.log(-math.log(u))


def hash_seed(s: str) -> int:
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


# ------------------------------------------------------------------
# 2. Scale table -- identical 9 scales already hand-picked in
#    esp-idf-link/main/bass_engine.cpp (kept 1:1 so the embedded port
#    needs zero changes here).
# ------------------------------------------------------------------
SCALES = [
    ("dorian",         [0, 2, 3, 5, 7, 9, 10]),
    ("aeolian",        [0, 2, 3, 5, 7, 8, 10]),
    ("phrygian",       [0, 1, 3, 5, 7, 8, 10]),
    ("minorPentatonic",[0, 3, 5, 7, 10, 3, 5]),
    ("melodicMinor",   [0, 2, 3, 5, 7, 9, 11]),
    ("mixolydian",     [0, 2, 4, 5, 7, 9, 10]),
    ("phrygianDom",    [0, 1, 4, 5, 7, 8, 10]),
    ("harmonicMinor",  [0, 2, 3, 5, 7, 8, 11]),
    ("hungarianMinor", [0, 2, 3, 6, 7, 8, 11]),
]


def sc(scale_idx: int, degree: int) -> int:
    name, degs = SCALES[scale_idx]
    return degs[degree % len(degs)]


def chord_tones(scale_idx: int) -> List[int]:
    """Root/3rd/5th/7th built by stacking scale-degree thirds, like SUBSTRATE buildChords()."""
    return [0, sc(scale_idx, 2), sc(scale_idx, 4), sc(scale_idx, 6)]


# Generic chord progressions (scale-degree roots), genre-agnostic --
# replaces the old per-genre GCFG.progs table in bass_engine.cpp.
PROGRESSIONS = [
    [0, 5, 3, 6],   # i - VI - iv - VII
    [0, 6, 5, 6],   # i - VII - VI - VII
    [0, 3, 6, 2],   # i - iv - VII - III
    [0, 5, 6, 4],   # i - VI - VII - V (dark cadence)
    [0, 2, 6, 3],   # i - III - VII - iv
    [0, 0, 5, 6],   # pedal-leaning: i - i - VI - VII
]


# ------------------------------------------------------------------
# 3. Groove templates -- 16th-note onset-probability, ported/condensed
#    from SUBSTRATE's GROOVES table down to a curated set that reads
#    well on a *bass* voice (dropped the busiest DnB/footwork templates
#    that only make sense with a drum bed underneath).
# ------------------------------------------------------------------
GROOVES: List[Tuple[str, List[float]]] = [
    ("minimal",     [1, 0, 0, 0,  0, 0, .3, 0,  .5, 0, 0, 0,  0, 0, .3, 0]),
    ("deep",        [1, 0, 0, 0,  0, 0, .36, 0, .6, 0, 0, 0,  0, 0, .3, 0]),
    ("house_bump",  [.92, 0, .78, .06, .22, 0, .78, .06, .58, 0, .78, .06, .22, 0, .78, .16]),
    ("four_floor",  [1, 0, .1, .16, .9, 0, .1, .16, .95, 0, .1, .16, .9, 0, .16, .32]),
    ("acid_303",    [1, .36, .7, .4, .6, .46, .76, .36, .86, .4, .7, .46, .6, .4, .8, .56]),
    ("garage_2step",[1, 0, .16, .56, .1, .2, .8, .16, .36, 0, .7, .2, .16, .56, .36, .5]),
    ("dembow",      [1, 0, 0, .86, 0, 0, .9, 0, .36, 0, 0, .86, 0, 0, .76, .2]),
    ("funk_synco",  [1, 0, .36, .7, .2, .46, .16, .6, .56, .2, .5, .36, .3, .6, .4, .56]),
    ("techno_roll", [1, .6, .66, .6, .8, .6, .66, .6, .9, .6, .66, .6, .8, .6, .7, .66]),
]

# 4/4 metric weight of each 16th (kick/backbeat-adjacent slots are "strong").
METRIC = [1, .12, .34, .16, .78, .12, .36, .16, .9, .12, .34, .16, .72, .14, .4, .28]


# ------------------------------------------------------------------
# 4. Pitch contour shapes, x in [0,1] -> target in [0,1]. Subset of
#    SUBSTRATE's CURVES chosen to be cheap to evaluate on an ESP32
#    (no per-sample cost -- evaluated once per 16-step motif).
# ------------------------------------------------------------------
def _level(x): return 0.5
def _climb(x): return x
def _descend(x): return 1 - x
def _arch(x): return math.sin(math.pi * x)
def _valley(x): return 1 - math.sin(math.pi * x)
def _wave(x): return (1 - math.cos(2 * math.pi * x)) / 2
def _zigzag(x): return 1 - abs(((x * 2) % 2) - 1)
def _terraced(x): return math.floor(x * 4) / 3

CONTOURS = [
    ("level", _level), ("climb", _climb), ("descend", _descend),
    ("arch", _arch), ("valley", _valley), ("wave", _wave),
    ("zigzag", _zigzag), ("terraced", _terraced),
]


# ------------------------------------------------------------------
# 5. The 8-dial parameter block
# ------------------------------------------------------------------
@dataclass
class Dials:
    harmony_gravity: float = 0.66
    harmony_color: float = 0.32
    groove_energy: float = 0.5
    groove_swing: float = 0.36
    motion_contour: float = 0.5
    motion_variation: float = 0.36
    voice_sweep: float = 0.46
    voice_artic: float = 0.4


@dataclass
class Note:
    step: int          # 0..15
    note: int           # MIDI, relative to nothing -- absolute already
    vel: int
    dur: float           # in steps
    fcc: int             # 0-127 filter CC value
    accent: bool = False
    ghost: bool = False
    slide: bool = False
    pitch_bend: float = 0.0


# ------------------------------------------------------------------
# 6. Rhythm: blend a groove template toward density/syncopation driven
#    by groove_energy + groove_swing, exactly like SUBSTRATE buildRhythm.
# ------------------------------------------------------------------
def build_onsets(dials: Dials, rng: Mulberry32) -> List[int]:
    energy = dials.groove_energy
    sync = dials.groove_swing  # swing dial also pushes syncopation, folded together

    n_templates = len(GROOVES)
    idx = min(n_templates - 1, int(energy * n_templates))
    name, tmpl = GROOVES[idx]

    density = 0.25 + energy * 0.65  # low energy = sparse, high = dense
    onsets = []
    for i in range(16):
        w = tmpl[i]
        w = w * (1 - sync * METRIC[i] * 0.7) + sync * (1 - METRIC[i]) * 0.6
        p = min(max(w * (0.4 + 1.55 * density) - (1 - density) * 0.16, 0), 1)
        if rng.rc(p):
            onsets.append(i)
    if 0 not in onsets and rng.rc(0.7):
        onsets.insert(0, 0)
    if not onsets:
        onsets = [0]
    return sorted(set(onsets))


# ------------------------------------------------------------------
# 7. Pitch: constrained DP (Viterbi) over a small candidate pool,
#    identical structure to SUBSTRATE's buildPitches / Liu's eq.28.
# ------------------------------------------------------------------
def build_pitches(onsets: List[int], root: int, scale_idx: int, dials: Dials,
                   rng: Mulberry32, register_span: int = 15) -> List[int]:
    gravity = dials.harmony_gravity
    tension = 1 - dials.harmony_color  # color dial also loosens chord-tone strictness
    depth = 0.4 + dials.motion_variation * 0.3
    variation_temp = dials.motion_variation * 0.85
    leap = 3 + int(dials.motion_variation * 9)   # comfortable leap, semitones
    oct_appetite = dials.motion_variation * 0.5
    repeat = 1 - dials.motion_variation * 0.6

    ctones = chord_tones(scale_idx)
    scale_set = {sc(scale_idx, d) % 12 for d in range(7)}

    lo, hi = root, root + register_span
    pool = list(range(lo, hi + 1))

    contour_name, contour_fn = CONTOURS[min(len(CONTOURS) - 1,
                                              int(dials.motion_contour * len(CONTOURS)))]
    n = len(onsets)
    targets = [lo + contour_fn(t / max(1, n - 1)) * register_span for t in range(n)]

    def harmonic_cost(m):
        pc = (m - root) % 12
        if pc == ctones[0] % 12:
            return 0.0
        if pc == ctones[2] % 12:
            return 0.09
        if pc == ctones[1] % 12:
            return 0.14
        if pc == ctones[3] % 12:
            return 0.21
        if pc in scale_set:
            return 0.52 * (1 - 0.75 * (1 - tension))
        return 1.05  # chromatic (unused here -- pool is scale-restricted for embedded simplicity)

    scale_pool = [m for m in pool if (m - root) % 12 in scale_set] or pool
    K = len(scale_pool)

    emit = [[0.0] * K for _ in range(n)]
    for t, s in enumerate(onsets):
        m_weight = METRIC[s % 16]
        for k, cand in enumerate(scale_pool):
            e = harmonic_cost(cand) * (0.3 + 0.7 * m_weight * gravity * 1.35)
            dev = abs(cand - targets[t]) / max(1, register_span)
            e += depth * dev * dev * 2.2
            e += variation_temp * rng.gumbel() * 0.5
            emit[t][k] = e

    def trans(a, b):
        d = abs(a - b)
        if d == 0:
            return (1 - repeat) * 0.75
        if d == 12:
            return 0.42 * (1 - oct_appetite) + 0.05
        c = 0.12 + (d / 12) * 0.6
        if d > leap:
            c += (d - leap) * 0.32
        if d in (1, 2):
            c *= 0.72
        return c

    dp = [[0.0] * K for _ in range(n)]
    bp = [[0] * K for _ in range(n)]
    dp[0] = emit[0][:]
    for t in range(1, n):
        for k in range(K):
            best, bi = math.inf, 0
            ck = scale_pool[k]
            for j in range(K):
                v = dp[t - 1][j] + 1.5 * trans(scale_pool[j], ck)
                if v < best:
                    best, bi = v, j
            dp[t][k] = best + emit[t][k]
            bp[t][k] = bi

    bi = min(range(K), key=lambda k: dp[n - 1][k])
    out = [0] * n
    for t in range(n - 1, -1, -1):
        out[t] = scale_pool[bi]
        bi = bp[t][bi]
    return out


# ------------------------------------------------------------------
# 8. Articulation: swing offset, accent/ghost/slide, filter-CC sweep.
# ------------------------------------------------------------------
def build_notes(onsets: List[int], pitches: List[int], dials: Dials,
                 rng: Mulberry32, root: int) -> List[Note]:
    artic = dials.voice_artic
    sweep_depth = dials.voice_sweep
    contour_name, contour_fn = CONTOURS[min(len(CONTOURS) - 1,
                                              int(dials.motion_contour * len(CONTOURS)))]

    accent_p = 0.15 + artic * 0.45
    ghost_p = 0.05 + artic * 0.35
    slide_p = artic * 0.4
    gate = 0.45 + (1 - artic) * 0.35

    notes: List[Note] = []
    for i, s in enumerate(onsets):
        m = METRIC[s % 16]
        next_s = onsets[i + 1] if i + 1 < len(onsets) else 16
        gap = next_s - s
        dur = max(0.15, min(gap * gate * (1 + (rng.next() - 0.5) * 0.5), gap * 1.2))
        accent = rng.rc(accent_p * (0.3 + 1.1 * m))
        ghost = (not accent) and rng.rc(ghost_p * (1.15 - m))
        slide = (gap <= 3) and (i + 1 < len(onsets)) and rng.rc(slide_p)
        vel = int(min(127, max(20, (0.55 + m * 0.3) * 100 + (25 if accent else 0) - (45 if ghost else 0))))
        x = s / 16.0
        curve_val = contour_fn(x)
        fcc = int(min(127, max(10, 40 + curve_val * 60 * (0.4 + sweep_depth) + (25 if accent else 0))))
        notes.append(Note(step=s, note=pitches[i], vel=vel, dur=dur, fcc=fcc,
                           accent=accent, ghost=ghost, slide=slide))
    return notes


# ------------------------------------------------------------------
# 9. Top-level: generate one bar (16 steps) -- this is exactly the
#    function signature the C++ port (genInterpreted) mirrors.
# ------------------------------------------------------------------
def generate_bar(root: int, scale_idx: int, dials: Dials, rng: Mulberry32) -> List[Note]:
    onsets = build_onsets(dials, rng)
    pitches = build_pitches(onsets, root, scale_idx, dials, rng)
    return build_notes(onsets, pitches, dials, rng, root)


def pick_scale_idx(dials: Dials) -> int:
    return min(len(SCALES) - 1, int(dials.harmony_color * len(SCALES)))


def generate_phrase(root: int, dials: Dials, seed: str, bars: int = 8) -> Tuple[List[Note], List[int], int]:
    """Multi-bar loop: rotates a chord progression over `bars` bars, one
    Note list of absolute-step positions (step = bar*16 + local_step)."""
    rng = Mulberry32(hash_seed(seed))
    scale_idx = pick_scale_idx(dials)
    prog = PROGRESSIONS[rng.ri(len(PROGRESSIONS))]
    phrase: List[Note] = []
    for bar in range(bars):
        degree = prog[bar % len(prog)]
        bar_root = root + sc(scale_idx, degree)
        bar_notes = generate_bar(bar_root, scale_idx, dials, rng)
        for note in bar_notes:
            phrase.append(Note(step=bar * 16 + note.step, note=note.note, vel=note.vel,
                                dur=note.dur, fcc=note.fcc, accent=note.accent,
                                ghost=note.ghost, slide=note.slide))
    return phrase, prog, scale_idx


# ------------------------------------------------------------------
# 10. Metrics -- same shape as SUBSTRATE's #metrics panel, used to
#     score experiment renders automatically.
# ------------------------------------------------------------------
def compute_metrics(phrase: List[Note], root: int, scale_idx: int) -> dict:
    if not phrase:
        return {}
    n = len(phrase)
    ctones = set(x % 12 for x in chord_tones(scale_idx))
    chord_tone_hits = sum(1 for note in phrase if (note.note - root) % 12 in ctones)
    weak = sum(1 for note in phrase if METRIC[note.step % 16] < 0.4)
    intervals = [abs(phrase[i].note - phrase[i - 1].note) for i in range(1, n)]
    mean_interval = sum(intervals) / max(1, len(intervals))
    accents = sum(1 for note in phrase if note.accent)
    ghosts = sum(1 for note in phrase if note.ghost)
    slides = sum(1 for note in phrase if note.slide)
    return {
        "n_notes": n,
        "chord_tone_pct": round(100 * chord_tone_hits / n, 1),
        "syncopation_pct": round(100 * weak / n, 1),
        "mean_interval_st": round(mean_interval, 2),
        "range_st": max(note.note for note in phrase) - min(note.note for note in phrase),
        "accent_pct": round(100 * accents / n, 1),
        "ghost_pct": round(100 * ghosts / n, 1),
        "slide_pct": round(100 * slides / n, 1),
    }
