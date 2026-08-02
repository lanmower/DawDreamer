# Resonaut

Resonaut is a modal/physical-modeling synthesizer built entirely with
[DawDreamer](https://github.com/DBraun/DawDreamer)'s Faust integration. It's an
independent, from-scratch alternative to Reason Studios' **Objekt** modeling
synthesizer: it shares Objekt's core idea (drive one or more virtual
resonating "objects" with a shared exciter, synthesizing everything in real
time instead of playing back samples) but is a new implementation on top of
Faust's modal-synthesis primitives, not a clone or port of Objekt's
(proprietary, closed-source) DSP.

## How it maps to Objekt

| Objekt concept | Resonaut equivalent |
|---|---|
| Exciter (impact stick <-> noisy wash) | `Exciter/Character` crossfades a filtered impact transient (`pm.strike`) with a gated, filtered noise wash |
| 3 resonating objects, each with a material | `Object 1/2/3`, each assigned a `Material`: `STRING`, `BAR`, `MEMBRANE`, or `PLATE` |
| Real-time synthesis, no samples | Every sound is generated from a bank of resonant filters (`pm.modeFilter`) driven by the exciter -- no audio files involved |
| Structural/material macros | `Stretch` (inharmonicity) and `Damping` (brightness/decay falloff) reshape a material continuously without changing its underlying mode table |
| Strike/excitation position | `Position` (both on the exciter and per-object) weights which modes speak loudest, the same trick `pm.strike`/`pm.modeFilter`-based instruments in Faust use |
| Amp envelope, filter, built-in reverb | `Amp/*`, `Filter/*`, `Reverb/*` sections |
| Polyphony | Delegated to DawDreamer's `FaustProcessor` polyphonic voice engine (`num_voices`, `group_voices`) |

## Signal flow

```
Exciter (impact <-> noise wash)
    |  (per-object "excite" send)
    v
Object 1 --\
Object 2 ----+--> mix --> amp envelope --> filter --> reverb --> master limiter --> out
Object 3 --/
```

Each object is a bank of 8 resonant filters tuned to the mode ratios of its
material (see `MODE_TABLES` in `resonaut.py`):

* **STRING** -- the ideal harmonic series (1, 2, 3, 4, ...).
* **BAR** -- transverse modes of a free-free bar (marimba/xylophone-like);
  ratios from the classical bar eigenvalue sequence.
* **MEMBRANE** -- axisymmetric modes of an ideal circular membrane (ratios of
  zeros of the Bessel function J0), drum-skin-like.
* **PLATE** -- a denser, more inharmonic/metallic spectrum in the spirit of a
  struck plate or bell.

These tables are physically-informed approximations, not exact PDE
solutions -- the same spirit in which Objekt (and most musical modal
synths) trades exactness for a playable, sound-designable instrument.

## Usage

```python
import dawdreamer as daw
from resonaut import Resonaut, Material

engine = daw.RenderEngine(44100, 512)

r = Resonaut(engine, num_voices=8, materials={
    1: Material.STRING,
    2: Material.BAR,
    3: Material.MEMBRANE,
})

r.set_exciter(character=0.1, sharpness=0.7, position=0.3, tone=8000)
r.set_object(1, decay=2.5, damping=0.9, stretch=0.05, level=0.8, excite=1.0)
r.set_object(2, transpose=12, decay=1.0, level=0.4, excite=0.6)
r.set_reverb(mix=0.25, size=0.6)

r.add_midi_note(60, 100, start_sec=0.0, duration_sec=1.0)  # C4

engine.load_graph(r.graph())
engine.render(2.0)
audio = engine.get_audio()
```

Switching an object's material rebuilds and recompiles the underlying Faust
DSP (the mode bank is unrolled at compile time), so it's a heavier operation
than setting a slider:

```python
r.set_material(2, Material.PLATE)
```

Every other parameter (`set_exciter`, `set_object`, `set_amp_env`,
`set_filter`, `set_reverb`, `set_master_gain`) is a plain Faust slider and can
be automated per-sample with `faust_processor.set_automation(...)` via
`r.processor`, exactly like any other DawDreamer `FaustProcessor`.

Run `python demo.py` to render a handful of example patches (plucked string,
struck bar, struck membrane, a noise-wash plate pad, and a layered chord) to
`./output/*.wav`.

## Files

* `resonaut.py` -- the Faust DSP generator (`build_dsp`) and the `Resonaut`
  Python wrapper class.
* `demo.py` -- renders example patches showcasing each material and the
  exciter's impact/wash range.

## Notes

* This synth stacks 3 resonating objects x up to 8 modes x N polyphonic
  voices, so leave some output headroom; the built-in master limiter
  (`co.limiter_1176_R4_stereo`) guards against hard digital clipping, but for
  sustained multi-object pads at high polyphony you may still want to trim
  `set_master_gain(...)` or normalize the render afterward (see `demo.py`'s
  `render()` helper).
* Requires DawDreamer built with Faust support (the default `pip install -e .`
  / prebuilt wheel already includes it).
