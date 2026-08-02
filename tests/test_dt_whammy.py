from dawdreamer_utils import *

DSP_PATH = abspath(FAUST_DSP / "dt_whammy.dsp")


def _param_address(faust_processor, suffix):
    """Find the full Faust parameter address whose name ends with `suffix`.

    Grouped vs. ungrouped voices, and mono vs. polyphonic compilation, all produce
    different address prefixes (e.g. "/DT_Whammy/mix" vs.
    "/Sequencer/DSP1/Polyphonic/Voices/DT_Whammy/mix"), so we search instead of
    hardcoding a path.
    """
    matches = [
        p["name"] for p in faust_processor.get_parameters_description() if p["name"].endswith(suffix)
    ]
    assert matches, f"no Faust parameter ending with {suffix!r} was found"
    return matches


def _set_all(faust_processor, suffix, value):
    for address in _param_address(faust_processor, suffix):
        faust_processor.set_parameter(address, value)


def _set_all_automation(faust_processor, suffix, data):
    for address in _param_address(faust_processor, suffix):
        faust_processor.set_automation(address, data)


def _make_source(engine, duration):
    """A real-world audio signal to run through the whammy: a funk drum loop."""
    data = load_audio_file(
        ASSETS / "575854__yellowtree__d-b-funk-loop.wav", duration=duration
    )
    return engine.make_playback_processor("source", data)


def test_dt_whammy_bypass_is_identity():
    """With bypass on and harmony_mode off, the mono pedal effect must be transparent."""
    duration = 1.0
    engine = daw.RenderEngine(SAMPLE_RATE, buffer_size=64)

    source = _make_source(engine, duration)

    faust_processor = engine.make_faust_processor("whammy")
    faust_processor.set_dsp(DSP_PATH)
    faust_processor.num_voices = 0  # mono pedal mode: process() runs once, no MIDI poly.
    faust_processor.compile()

    _set_all(faust_processor, "/bypass", 1)
    _set_all(faust_processor, "/pedal", 1)  # should have no effect while bypassed

    engine.load_graph([(source, []), (faust_processor, ["source"])])
    render(engine, duration=duration)

    dry = load_audio_file(ASSETS / "575854__yellowtree__d-b-funk-loop.wav", duration=duration)
    wet = engine.get_audio()

    n = min(dry.shape[1], wet.shape[1])
    assert np.allclose(dry[:, :n], wet[:, :n], atol=1e-5)


def test_dt_whammy_mono_pedal_dive():
    """Riding the pedal from heel to toe should smoothly change the pitch shift."""
    duration = 2.0
    engine = daw.RenderEngine(SAMPLE_RATE, buffer_size=64)

    source = _make_source(engine, duration)

    faust_processor = engine.make_faust_processor("whammy")
    faust_processor.set_dsp(DSP_PATH)
    faust_processor.num_voices = 0
    faust_processor.compile()

    _set_all(faust_processor, "/bypass", 0)
    _set_all(faust_processor, "/mix", 1)
    _set_all(faust_processor, "/mode", 0)  # Octave Up
    _set_all(faust_processor, "/glide_ms", 12)
    _set_all(faust_processor, "/window_ms", 15)  # ultra-low-latency window

    # Slow "dive" sweep: heel (0) to toe (1) over the whole render, like riding a real
    # Whammy expression pedal.
    pedal_ramp = np.linspace(0.0, 1.0, int(duration * SAMPLE_RATE))
    _set_all_automation(faust_processor, "/pedal", pedal_ramp)

    engine.load_graph([(source, []), (faust_processor, ["source"])])
    render(engine, file_path=OUTPUT / "test_dt_whammy_mono_pedal_dive.wav", duration=duration)

    audio = engine.get_audio()
    assert np.mean(np.abs(audio)) > 1e-4

    # A full pedal dive (up an octave) must differ audibly from the un-shifted (pedal=0) case.
    engine2 = daw.RenderEngine(SAMPLE_RATE, buffer_size=64)
    source2 = _make_source(engine2, duration)
    faust_processor2 = engine2.make_faust_processor("whammy")
    faust_processor2.set_dsp(DSP_PATH)
    faust_processor2.num_voices = 0
    faust_processor2.compile()
    _set_all(faust_processor2, "/bypass", 0)
    _set_all(faust_processor2, "/mix", 1)
    _set_all(faust_processor2, "/mode", 0)
    _set_all(faust_processor2, "/pedal", 0)
    engine2.load_graph([(source2, []), (faust_processor2, ["source"])])
    render(engine2, duration=duration)
    static_audio = engine2.get_audio()

    n = min(audio.shape[1], static_audio.shape[1])
    assert not np.allclose(audio[:, :n], static_audio[:, :n], atol=1e-3)


def test_dt_whammy_polyphonic_harmony_chord():
    """Holding several MIDI keys should harmonize the same input into a chord."""
    duration = 2.0
    engine = daw.RenderEngine(SAMPLE_RATE, buffer_size=64)

    source = _make_source(engine, duration)

    faust_processor = engine.make_faust_processor("whammy")
    faust_processor.set_dsp(DSP_PATH)
    faust_processor.group_voices = True
    faust_processor.num_voices = 4
    faust_processor.compile()

    _set_all(faust_processor, "/harmony_mode", 1)
    _set_all(faust_processor, "/root_note", 60)  # middle C = unison / no shift
    _set_all(faust_processor, "/glide_ms", 8)
    _set_all(faust_processor, "/window_ms", 15)

    # A held C major triad: 60 (unison/dry-ish), 64 (major 3rd up), 67 (5th up).
    faust_processor.add_midi_note(60, 100, 0.0, duration)
    faust_processor.add_midi_note(64, 100, 0.0, duration)
    faust_processor.add_midi_note(67, 100, 0.0, duration)

    engine.load_graph([(source, []), (faust_processor, ["source"])])
    render(
        engine, file_path=OUTPUT / "test_dt_whammy_polyphonic_harmony_chord.wav", duration=duration
    )

    audio = engine.get_audio()
    assert np.mean(np.abs(audio)) > 1e-4

    # The 3-note chord must differ from a single unison note (which is close to dry).
    engine2 = daw.RenderEngine(SAMPLE_RATE, buffer_size=64)
    source2 = _make_source(engine2, duration)
    faust_processor2 = engine2.make_faust_processor("whammy")
    faust_processor2.set_dsp(DSP_PATH)
    faust_processor2.group_voices = True
    faust_processor2.num_voices = 4
    faust_processor2.compile()
    _set_all(faust_processor2, "/harmony_mode", 1)
    _set_all(faust_processor2, "/root_note", 60)
    faust_processor2.add_midi_note(60, 100, 0.0, duration)
    engine2.load_graph([(source2, []), (faust_processor2, ["source"])])
    render(engine2, duration=duration)
    unison_audio = engine2.get_audio()

    n = min(audio.shape[1], unison_audio.shape[1])
    assert not np.allclose(audio[:, :n], unison_audio[:, :n], atol=1e-3)
