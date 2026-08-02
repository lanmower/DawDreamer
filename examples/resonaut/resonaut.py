"""Resonaut: a modal-synthesis instrument built on DawDreamer + Faust.

Resonaut is a from-scratch alternative to Reason Studios' "Objekt" modeling
synthesizer. Like Objekt, it makes sound by driving one or more virtual
resonating *objects* with a shared *exciter*, instead of playing back
samples. There is no attempt to bit-match Objekt (its DSP is proprietary);
this is an independent implementation of the same idea -- physical/modal
modeling -- using Faust's modal-resonator primitives (``pm.modeFilter``,
``pm.strike``) plus mode-frequency tables that approximate the vibration
spectra of strings, bars, membranes, and plates.

Signal flow, mirroring Objekt's architecture:

    Exciter (impact <-> noise wash)
        |  (per-object "excite" send)
        v
    Object 1 --\\
    Object 2 ----+--> mix --> amp envelope --> filter --> reverb --> out
    Object 3 --/

Each Object is a bank of ``N_MODES`` resonant filters tuned to the
characteristic mode ratios of a chosen ``Material`` (STRING, BAR, MEMBRANE,
or PLATE), re-pitched by the played note. Two continuous macros reshape the
material in real time without changing its mode count:

* ``stretch``  -- warps the mode ratios apart/together (inharmonicity).
* ``damping``  -- makes higher modes decay faster than lower ones
  (brightness/material-damping).

A ``position`` control per object (and per exciter) weights which modes
speak loudest, approximating where an object is struck/heard, the same way
Faust's own ``pm.strike`` uses excitation position.

This module only builds/configures the Faust DSP graph; it relies on
DawDreamer's ``FaustProcessor`` for compilation, polyphony, and rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

N_MODES = 8


class Material(Enum):
    """The vibrating-object archetypes offered for each of the 3 object slots."""

    STRING = "string"
    BAR = "bar"
    MEMBRANE = "membrane"
    PLATE = "plate"


# Mode ratios (relative to the fundamental) and relative mode gains for each
# material. These are physically-informed approximations of well-known mode
# families, not exact PDE solutions:
#
#   STRING    -- ideal harmonic series.
#   BAR       -- transverse modes of a free-free Euler-Bernoulli bar
#                (marimba/xylophone-like), from the classical bar eigenvalue
#                sequence.
#   MEMBRANE  -- axisymmetric modes of an ideal circular membrane, i.e.
#                ratios of zeros of the Bessel function J0 (drum-skin-like).
#   PLATE     -- a denser, more inharmonic/metallic spectrum in the spirit of
#                a struck plate or bell.
MODE_TABLES: dict[Material, tuple[list[float], list[float]]] = {
    Material.STRING: (
        [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
        [1.0, 0.6, 0.4, 0.3, 0.22, 0.17, 0.13, 0.1],
    ),
    Material.BAR: (
        [1.0, 2.756, 5.404, 8.933, 13.344, 18.636, 24.501, 31.291],
        [1.0, 0.55, 0.3, 0.18, 0.1, 0.06, 0.04, 0.03],
    ),
    Material.MEMBRANE: (
        [1.000, 1.594, 2.136, 2.296, 2.653, 2.918, 3.156, 3.501],
        [1.0, 0.7, 0.5, 0.4, 0.32, 0.26, 0.22, 0.18],
    ),
    Material.PLATE: (
        [1.0, 1.99, 2.83, 3.86, 4.60, 5.51, 6.82, 7.90],
        [1.0, 0.65, 0.5, 0.4, 0.32, 0.26, 0.2, 0.16],
    ),
}


@dataclass
class ObjectConfig:
    """Structural defaults for one of the 3 resonating objects."""

    material: Material = Material.STRING
    transpose: float = 0.0  # semitones relative to the played note
    decay: float = 1.2  # seconds (T60 of the fundamental mode)
    damping: float = 0.85  # per-mode T60 falloff, 1.0 = no falloff
    stretch: float = 0.0  # inharmonicity exponent applied to mode ratios
    position: float = 0.5  # 0-1, which modes speak loudest
    excite: float = 1.0  # how much of the shared exciter reaches this object
    level: float = 1.0  # output mix level
    pan: float = 0.5  # 0=left, 1=right


def _mode_bank_dsp(prefix: str, material: Material) -> str:
    """Return Faust code for one object's bank of N_MODES resonant filters.

    `prefix` names the object's Faust parameters (already declared by the
    caller): `{prefix}_freq`, `{prefix}_decay`, `{prefix}_damping`,
    `{prefix}_stretch`, `{prefix}_position`.
    """
    ratios, gains = MODE_TABLES[material]
    assert len(ratios) == N_MODES and len(gains) == N_MODES

    terms = []
    for i in range(N_MODES):
        ratio = ratios[i]
        gain = gains[i]
        n = i + 1  # 1-indexed mode number, used for the position weighting
        freq_expr = f"({prefix}_freq*pow({ratio},1+{prefix}_stretch))"
        t60_expr = f"({prefix}_decay*pow({prefix}_damping,{i}))"
        pos_weight = f"abs(sin(ma.PI*{prefix}_position*{n}))"
        gain_expr = f"({gain}*{pos_weight})"
        terms.append(f"pm.modeFilter({freq_expr},{t60_expr},{gain_expr})")

    joined = ",\n      ".join(terms)
    return f"""({prefix}_excited <: (
      {joined}
    ) :> _)"""


def build_dsp(
    num_voices: int = 8,
    materials: dict[int, Material] | None = None,
    defaults: dict[int, ObjectConfig] | None = None,
) -> str:
    """Build the full Resonaut instrument as a Faust DSP program (text).

    `materials` maps object index (1, 2, 3) to a `Material`. Changing an
    object's material changes the generated DSP code (the mode bank is
    unrolled at compile time), so switching materials means rebuilding and
    recompiling the FaustProcessor; every other parameter is a plain Faust
    slider that can be changed live.

    `defaults` optionally overrides the initial values of each object's
    sliders (see `ObjectConfig`).
    """
    materials = materials or {1: Material.STRING, 2: Material.BAR, 3: Material.MEMBRANE}
    defaults = defaults or {}

    cfgs: dict[int, ObjectConfig] = {}
    for idx in (1, 2, 3):
        cfg = defaults.get(idx, ObjectConfig())
        cfg.material = materials.get(idx, cfg.material)
        cfgs[idx] = cfg

    # Only object 1 is "excited" and audible by default so a freshly-built
    # instrument makes a simple, plucked-string-like sound; objects 2 and 3
    # start silent (level 0) until the user dials them in, mirroring how an
    # empty Objekt slot doesn't contribute sound.
    cfgs[1].excite = defaults.get(1, ObjectConfig()).excite if 1 in defaults else 1.0
    cfgs[1].level = defaults.get(1, ObjectConfig()).level if 1 in defaults else 0.9
    for idx in (2, 3):
        if idx not in defaults:
            cfgs[idx].excite = 0.0
            cfgs[idx].level = 0.0

    object_blocks = []
    object_sums = []
    for idx in (1, 2, 3):
        cfg = cfgs[idx]
        p = f"obj{idx}"
        object_blocks.append(f"""
// ---- Object {idx}: {cfg.material.value} ----
{p}_transpose = hslider("h:Object {idx}/[0]Transpose",{cfg.transpose},-36,36,0.01);
{p}_decay     = hslider("h:Object {idx}/[1]Decay",{cfg.decay},0.01,20,0.001);
{p}_damping   = hslider("h:Object {idx}/[2]Damping",{cfg.damping},0.05,1.0,0.001);
{p}_stretch   = hslider("h:Object {idx}/[3]Stretch",{cfg.stretch},-0.5,1.5,0.001);
{p}_position  = hslider("h:Object {idx}/[4]Position",{cfg.position},0,1,0.001);
{p}_excite    = hslider("h:Object {idx}/[5]Excite",{cfg.excite},0,2,0.001);
{p}_level     = hslider("h:Object {idx}/[6]Level",{cfg.level},0,1.5,0.001);
{p}_pan       = hslider("h:Object {idx}/[7]Pan",{cfg.pan},0,1,0.001);

{p}_freq = freq*pow(2,{p}_transpose/12);
{p}_excited = exciter*{p}_excite;
{p}_bank = {_mode_bank_dsp(p, cfg.material)};
{p}_out = {p}_bank*{p}_level;
""")
        object_sums.append(f"obj{idx}_out")

    sum_expr = " + ".join(object_sums)

    dsp = f"""
declare name "Resonaut";
declare description "A modal-synthesis alternative to Reason's Objekt.";
declare options "[nvoices:{num_voices}]";
import("stdfaust.lib");

freq = hslider("freq",220,20,20000,0.01);
gain = hslider("gain",0.7,0,1,0.001);
gate = button("gate");

// ================= Exciter =================
// `character` morphs continuously between a short mechanical impact
// (a struck-object transient) and a sustained, filtered noise wash
// (a bowed/blown/brushed excitation), just like Objekt's single exciter
// macro that ranges "from the short impact of a stick to a wash of noisy
// static."
ex_character = hslider("h:Exciter/[0]Character",0.0,0,1,0.001);
ex_tone      = hslider("h:Exciter/[1]Tone",8000,200,18000,1);
ex_sharpness = hslider("h:Exciter/[2]Sharpness",0.5,0,1,0.001);
ex_position  = hslider("h:Exciter/[3]Position",0.3,0,1,0.001);
ex_level     = hslider("h:Exciter/[4]Level",0.8,0,2,0.001);
ex_wash_attack  = hslider("h:Exciter/[5]Wash Attack",0.02,0.001,2,0.001);
ex_wash_release = hslider("h:Exciter/[6]Wash Release",0.3,0.001,4,0.001);

impactSig = pm.strike(ex_position,ex_sharpness,1.0,gate) : fi.lowpass(2,ex_tone);
washSig   = no.noise : fi.lowpass(2,ex_tone) : *(en.asr(ex_wash_attack,1.0,ex_wash_release,gate));
exciter   = (impactSig*(1.0-ex_character) + washSig*ex_character)*ex_level;

{"".join(object_blocks)}

dry_mono = {sum_expr};

// ================= Amp envelope =================
amp_attack  = hslider("h:Amp/[0]Attack",0.001,0.0,5,0.0001);
amp_decay   = hslider("h:Amp/[1]Decay",0.05,0.0,10,0.0001);
amp_sustain = hslider("h:Amp/[2]Sustain",1.0,0.0,1,0.001);
amp_release = hslider("h:Amp/[3]Release",0.3,0.001,10,0.0001);

ampEnv = en.adsr(amp_attack,amp_decay,amp_sustain,amp_release,gate);

shaped_mono = dry_mono*ampEnv*gain;

// ================= Filter =================
filter_cutoff = hslider("h:Filter/[0]Cutoff",18000,20,20000,1);
filter_q      = hslider("h:Filter/[1]Resonance",0.7071,0.1,10,0.001);

filtered_mono = shaped_mono : fi.resonlp(filter_cutoff,filter_q,1.0);

// ================= Object panning (post-filter, per-object width) =================
// Objects were summed to mono above for a simple, robust filter/env chain;
// stereo width is reintroduced here from the average of the objects' pans.
avg_pan = (obj1_pan*obj1_level + obj2_pan*obj2_level + obj3_pan*obj3_level)
          / max(0.0001, obj1_level + obj2_level + obj3_level);

wide = filtered_mono : sp.panner(avg_pan);

// ================= Reverb =================
reverb_mix    = hslider("h:Reverb/[0]Mix",0.2,0,1,0.001);
reverb_size   = hslider("h:Reverb/[1]Size",0.5,0,1,0.001);
reverb_damp   = hslider("h:Reverb/[2]Damp",0.5,0,1,0.001);

wet  = filtered_mono*0.5,filtered_mono*0.5 : re.stereo_freeverb(reverb_size*0.9,reverb_size*0.9,reverb_damp,0);
wetL = wet : (_,!);
wetR = wet : (!,_);

outL = wide : (_,!);
outR = wide : (!,_);

process = (outL*(1.0-reverb_mix) + wetL*reverb_mix), (outR*(1.0-reverb_mix) + wetR*reverb_mix);

// ================= Master =================
// Polyphonic Faust DSPs must declare a stereo `effect` process; it runs
// once on the *summed* output of all voices, so it's the right place for
// a master trim + limiter (stacking many resonating objects, notes, and a
// sustained noise-wash exciter can otherwise clip hard). A real limiter
// (attack/release gain reduction) is used instead of a static waveshaper
// so it only engages on peaks instead of constantly coloring the sound.
master_gain = hslider("h:Master/[0]Gain",0.5,0,2,0.001);
effect(l,r) = (l*master_gain, r*master_gain) : co.limiter_1176_R4_stereo;
"""
    return dsp


class Resonaut:
    """Python-friendly wrapper around the generated Faust instrument.

    Wraps a `dawdreamer.FaustProcessor`, handling DSP (re)generation,
    compilation, and lets you address parameters by short logical names
    (e.g. `set_object(1, decay=2.0)`) instead of full Faust UI paths, which
    vary with DawDreamer/Faust version and with `group_voices`.
    """

    def __init__(
        self,
        engine,
        name: str = "resonaut",
        num_voices: int = 8,
        group_voices: bool = True,
        materials: dict[int, Material] | None = None,
        defaults: dict[int, ObjectConfig] | None = None,
    ):
        self.engine = engine
        self.name = name
        self.num_voices = num_voices
        self.group_voices = group_voices
        self.materials = dict(materials or {1: Material.STRING, 2: Material.BAR, 3: Material.MEMBRANE})
        self.defaults = dict(defaults or {})

        self.processor = engine.make_faust_processor(name)
        self._param_names: list[str] = []
        self._compile()

    def _compile(self):
        dsp = build_dsp(self.num_voices, self.materials, self.defaults)
        self.processor.set_dsp_string(dsp)
        self.processor.num_voices = self.num_voices
        self.processor.group_voices = self.group_voices
        self.processor.compile()
        self._param_names = [p["name"] for p in self.processor.get_parameters_description()]

    def _paths_ending_with(self, suffix: str) -> list[str]:
        matches = [n for n in self._param_names if n.endswith(suffix)]
        if not matches:
            raise KeyError(f"No Resonaut parameter path ends with {suffix!r}")
        return matches

    def _set(self, suffix: str, value: float):
        # When group_voices is False, one path per voice ends with `suffix`;
        # setting all of them keeps behavior identical to the grouped case.
        for path in self._paths_ending_with(suffix):
            self.processor.set_parameter(path, value)

    def set_material(self, index: int, material: Material):
        """Change an object's material. This rebuilds and recompiles the DSP
        (mode banks are unrolled at compile time), so any parameters not
        passed via `defaults` revert to their factory defaults."""
        if index not in (1, 2, 3):
            raise ValueError("Object index must be 1, 2, or 3.")
        self.materials[index] = material
        self._compile()

    def set_exciter(
        self,
        character: float | None = None,
        tone: float | None = None,
        sharpness: float | None = None,
        position: float | None = None,
        level: float | None = None,
        wash_attack: float | None = None,
        wash_release: float | None = None,
    ):
        for suffix, value in {
            "Exciter/Character": character,
            "Exciter/Tone": tone,
            "Exciter/Sharpness": sharpness,
            "Exciter/Position": position,
            "Exciter/Level": level,
            "Exciter/Wash_Attack": wash_attack,
            "Exciter/Wash_Release": wash_release,
        }.items():
            if value is not None:
                self._set(suffix, value)

    def set_object(
        self,
        index: int,
        transpose: float | None = None,
        decay: float | None = None,
        damping: float | None = None,
        stretch: float | None = None,
        position: float | None = None,
        excite: float | None = None,
        level: float | None = None,
        pan: float | None = None,
    ):
        if index not in (1, 2, 3):
            raise ValueError("Object index must be 1, 2, or 3.")
        for suffix, value in {
            f"Object_{index}/Transpose": transpose,
            f"Object_{index}/Decay": decay,
            f"Object_{index}/Damping": damping,
            f"Object_{index}/Stretch": stretch,
            f"Object_{index}/Position": position,
            f"Object_{index}/Excite": excite,
            f"Object_{index}/Level": level,
            f"Object_{index}/Pan": pan,
        }.items():
            if value is not None:
                self._set(suffix, value)

    def set_amp_env(
        self,
        attack: float | None = None,
        decay: float | None = None,
        sustain: float | None = None,
        release: float | None = None,
    ):
        for suffix, value in {
            "Amp/Attack": attack,
            "Amp/Decay": decay,
            "Amp/Sustain": sustain,
            "Amp/Release": release,
        }.items():
            if value is not None:
                self._set(suffix, value)

    def set_filter(self, cutoff: float | None = None, resonance: float | None = None):
        for suffix, value in {
            "Filter/Cutoff": cutoff,
            "Filter/Resonance": resonance,
        }.items():
            if value is not None:
                self._set(suffix, value)

    def set_reverb(
        self,
        mix: float | None = None,
        size: float | None = None,
        damp: float | None = None,
    ):
        for suffix, value in {
            "Reverb/Mix": mix,
            "Reverb/Size": size,
            "Reverb/Damp": damp,
        }.items():
            if value is not None:
                self._set(suffix, value)

    def set_master_gain(self, gain: float):
        self._set("Master/Gain", gain)

    def add_midi_note(self, note: int, velocity: int, start_sec: float, duration_sec: float):
        self.processor.add_midi_note(note, velocity, start_sec, duration_sec)

    def load_midi(self, *args, **kwargs):
        self.processor.load_midi(*args, **kwargs)

    def graph(self):
        """Convenience for `engine.load_graph(resonaut.graph())`."""
        return [(self.processor, [])]
