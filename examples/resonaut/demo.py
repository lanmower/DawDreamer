"""Render a handful of demo patches with Resonaut.

Run from this directory (or anywhere, since paths are absolute):

    python demo.py

Produces .wav files in ./output demonstrating:
  * a plucked-string note
  * a mallet-struck bar (marimba-like)
  * a struck membrane (drum-like)
  * a bowed/blown metallic plate pad using the noise-wash exciter
  * a layered patch (all 3 objects at once) played polyphonically via MIDI-style notes
"""

from pathlib import Path

import dawdreamer as daw
import numpy as np
from scipy.io import wavfile

from resonaut import Material, ObjectConfig, Resonaut

SAMPLE_RATE = 44100
BLOCK_SIZE = 512
OUTPUT = Path(__file__).parent / "output"


def render(r: Resonaut, engine: daw.RenderEngine, duration: float, file_name: str):
    engine.load_graph(r.graph())
    assert engine.render(duration)
    audio = engine.get_audio()
    peak = float(np.max(np.abs(audio))) or 1.0
    if peak > 0.98:
        audio = audio * (0.98 / peak)
    OUTPUT.mkdir(exist_ok=True)
    path = OUTPUT / file_name
    wavfile.write(path, SAMPLE_RATE, audio.T.astype(np.float32))
    print(f"wrote {path}  (peak was {peak:.3f})")


def plucked_string():
    engine = daw.RenderEngine(SAMPLE_RATE, BLOCK_SIZE)
    r = Resonaut(engine, num_voices=1, materials={1: Material.STRING})
    r.set_exciter(character=0.0, sharpness=0.8, position=0.15, tone=9000)
    r.set_object(1, decay=3.0, damping=0.92, stretch=0.02, level=0.9, excite=1.0)
    r.set_amp_env(attack=0.001, release=0.4)
    r.set_reverb(mix=0.15)
    r.add_midi_note(57, 100, 0.1, 3.0)  # A3
    render(r, engine, 4.0, "01_plucked_string.wav")


def struck_bar():
    engine = daw.RenderEngine(SAMPLE_RATE, BLOCK_SIZE)
    r = Resonaut(engine, num_voices=1, materials={1: Material.BAR})
    r.set_exciter(character=0.0, sharpness=1.0, position=0.5, tone=12000)
    r.set_object(1, decay=1.8, damping=0.8, stretch=0.0, level=0.9, excite=1.0)
    r.set_amp_env(attack=0.001, release=0.2)
    r.set_reverb(mix=0.25, size=0.7)
    r.add_midi_note(72, 110, 0.1, 2.0)  # C5
    render(r, engine, 3.0, "02_struck_bar.wav")


def struck_membrane():
    engine = daw.RenderEngine(SAMPLE_RATE, BLOCK_SIZE)
    r = Resonaut(engine, num_voices=1, materials={1: Material.MEMBRANE})
    r.set_exciter(character=0.05, sharpness=0.9, position=0.2, tone=4000)
    r.set_object(1, transpose=-12, decay=0.6, damping=0.55, stretch=0.0, level=1.0, excite=1.2)
    r.set_amp_env(attack=0.001, release=0.15)
    r.set_filter(cutoff=6000)
    r.add_midi_note(48, 120, 0.1, 1.0)  # C3
    render(r, engine, 2.0, "03_struck_membrane.wav")


def noise_wash_plate_pad():
    engine = daw.RenderEngine(SAMPLE_RATE, BLOCK_SIZE)
    r = Resonaut(engine, num_voices=4, materials={1: Material.PLATE})
    r.set_exciter(character=0.9, sharpness=0.3, tone=5000, wash_attack=0.6, wash_release=1.5)
    r.set_object(1, decay=4.0, damping=0.97, stretch=0.3, level=0.8, excite=1.0)
    r.set_amp_env(attack=0.8, release=2.0)
    r.set_reverb(mix=0.45, size=0.85, damp=0.3)
    r.add_midi_note(48, 90, 0.0, 3.0)
    r.add_midi_note(55, 80, 0.0, 3.0)
    r.add_midi_note(64, 70, 0.0, 3.0)
    render(r, engine, 6.0, "04_noise_wash_plate_pad.wav")


def layered_hybrid_chord():
    engine = daw.RenderEngine(SAMPLE_RATE, BLOCK_SIZE)
    r = Resonaut(
        engine,
        num_voices=6,
        materials={1: Material.STRING, 2: Material.BAR, 3: Material.MEMBRANE},
        defaults={
            1: ObjectConfig(decay=2.5, damping=0.9, level=0.7, excite=1.0),
            2: ObjectConfig(transpose=12, decay=1.2, damping=0.75, level=0.5, excite=0.8),
            3: ObjectConfig(transpose=-12, decay=0.5, damping=0.5, level=0.4, excite=0.6),
        },
    )
    r.set_exciter(character=0.15, sharpness=0.7, position=0.3, tone=8000)
    r.set_reverb(mix=0.3, size=0.6)
    for note, vel, start in [(48, 100, 0.0), (52, 90, 0.0), (55, 90, 0.0), (60, 100, 1.5)]:
        r.add_midi_note(note, vel, start, 1.4)
    render(r, engine, 3.5, "05_layered_hybrid_chord.wav")


if __name__ == "__main__":
    plucked_string()
    struck_bar()
    struck_membrane()
    noise_wash_plate_pad()
    layered_hybrid_chord()
