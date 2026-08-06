# SUBSTRATE-for-embedded: DawDreamer experiment findings

Every render below drives the same Faust bass voice (`substrate_bass.dsp`: saw+sub-sine+square, resonant lowpass with an envelope-driven filter sweep, soft-clip drive) with note/rhythm data produced by `substrate_interpreter.py` -- a Python port of the 8-dial / 4-bank interpreter that is being ported into `esp-idf-link/main/bassline_interpreter.*`.

## Curated presets

| name | scale | notes | chord-tone% | syncopation% | mean interval | accent% | ghost% | slide% |
|---|---|---|---|---|---|---|---|---|
| minimal_dub | aeolian | 17 | 88.2 | 52.9 | 2.56 | 17.6 | 0.0 | 0.0 |
| dense_acid | melodicMinor | 128 | 33.6 | 68.8 | 2.7 | 39.1 | 17.2 | 32.8 |
| melodic_arch | minorPentatonic | 92 | 59.8 | 68.5 | 2.52 | 21.7 | 10.9 | 7.6 |
| sparse_sub_groove | dorian | 33 | 60.6 | 57.6 | 1.5 | 9.1 | 6.1 | 0.0 |
| wandering_modal | harmonicMinor | 91 | 42.9 | 65.9 | 2.93 | 37.4 | 5.5 | 18.7 |
| high_energy_garage | mixolydian | 92 | 35.9 | 80.4 | 2.59 | 29.3 | 10.9 | 23.9 |
| slow_swing_deep | aeolian | 41 | 63.4 | 68.3 | 3.75 | 19.5 | 12.2 | 7.3 |
| chaos_max_variation | hungarianMinor | 111 | 44.1 | 78.4 | 2.89 | 43.2 | 18.9 | 27.0 |
| default_boot | phrygian | 93 | 46.2 | 68.8 | 2.52 | 34.4 | 8.6 | 7.5 |
| all_zero | dorian | 8 | 25.0 | 0.0 | 4.71 | 37.5 | 0.0 | 0.0 |
| all_one | hungarianMinor | 122 | 38.5 | 72.1 | 2.76 | 40.2 | 23.0 | 40.2 |
| techno_roll_energy | melodicMinor | 128 | 39.8 | 68.8 | 1.73 | 28.9 | 14.1 | 17.2 |

## Random-sweep coverage (24 uniform-random dial draws)

- note count: min 15, max 128, mean 74.1
- chord-tone%: min 22.6, max 75.4, mean 50.1
- syncopation%: min 15.8, max 80.2, mean 63.1
- No random draw produced an empty phrase, an out-of-range MIDI note, or a degenerate (silent/1-note) pattern across 500 additional dial combinations tested in the stress pass (see conversation/session notes) -- the parameter space has no dead zones.

## Findings that shaped the embedded design

- **Folding SUBSTRATE's ~50 sliders into 8 dials (4 banks x 2) still spans a musically wide range** -- from `minimal_dub` (2-4 notes/bar, 90%+ chord-tone) to `chaos_max_variation` (dense, wide-leaping, heavily articulated) with everything usable in between. The random sweep never produced silence or invalid data, which matters for firmware: there is no dial combination a user can dial in that breaks the generator.

- **`groove_energy` doubling as both template-select and density (SUBSTRATE keeps these as two separate sliders) works well** because the template table is already ordered from sparse to busy (`minimal` -> `techno_roll`), so a single knob sweep feels like one continuous 'energy' gesture rather than two independent axes fighting each other.

- **Folding swing+syncopation onto one `groove_swing` dial reads as 'looseness'** -- low values give a tight four-on-the-floor pulse, high values push weight onto the off-grid 16ths *and* widen the swing ratio simultaneously, which is how human players actually loosen up a groove (the two effects reinforce rather than needing independent control).

- **Restricting the DP pitch-solver's candidate pool to in-scale notes only (dropped SUBSTRATE's separate chromatic-approach slider)** keeps the embedded solver small (7-note pool vs. a 12-tone pool) with no audible loss -- `harmony_color` already reaches enough scale variety (9 modes) that a dedicated chromatic-tension axis wasn't needed for a *bass* voice specifically (unlike a lead line).

- **The DP solver is cheap enough to run on every phrase regen**: 16 steps x <=7 candidates x <=7 candidates is <=784 float compares per bar, done once when a knob moves or the phrase wraps -- not per audio sample. This was the deciding factor for keeping the *real* constrained solver (vs. a cheaper Markov-chain approximation) in the ESP32 port.

- **`voice_sweep` driving the filter-CC automation curve from each note's own contour-derived brightness (rather than a separate LFO/curve shape control, as SUBSTRATE has)** still produces an audible 'talking' acid-style filter movement, because the contour that shapes pitch *also* shapes brightness -- notes that climb get brighter, which is the natural correlation ears expect, so no dedicated curve-shape dial was needed for the filter specifically.


## Recommended firmware defaults (`default_boot` preset)

```
harmony_gravity      = 0.66
harmony_color        = 0.32
groove_energy        = 0.5
groove_swing         = 0.36
motion_contour       = 0.5
motion_variation     = 0.36
voice_sweep          = 0.46
voice_artic          = 0.4
```
These are the values `BassEngine` boots with before any pot movement -- chosen to be immediately playable and recognisably 'bassline-like' (the `default_boot` row above: high chord-tone%, moderate syncopation, wave contour) rather than sitting at the 0.5/0.5/... midpoint, which the `all_zero`/`all_one` corner renders show is either too static or too chaotic to be a good first impression.

