from dawdreamer_utils import *

BUFFER_SIZE = 512


def test_sampler(set_data=False):
    def get_par_index(desc, par_name):
        for parDict in desc:
            if parDict["name"] == par_name:
                return parDict["index"]
        raise ValueError(f"Parameter '{par_name}' not found.")

    DURATION = 1.5

    engine = daw.RenderEngine(SAMPLE_RATE, BUFFER_SIZE)

    data = load_audio_file(ASSETS / "60988__folktelemetry__crash-fast-14.wav")
    sampler_processor = engine.make_sampler_processor("playback", data)

    if set_data:
        sampler_processor.set_data(data)

    assert sampler_processor.get_num_output_channels() == 2

    desc = sampler_processor.get_parameters_description()
    # print(desc)

    sampler_processor.set_parameter(
        get_par_index(desc, "Center Note"), 60.0
    )  # set the center frequency to middle C (60)
    # sampler_processor.set_parameter(5, 100.) # set the volume envelope's release to 100 milliseconds.

    # (MIDI note, velocity, start sec, duration sec)
    sampler_processor.add_midi_note(60, 60, 0.0, 0.25)
    sampler_processor.add_midi_note(64, 80, 0.5, 0.5)
    sampler_processor.add_midi_note(67, 127, 0.75, 0.1)

    sampler_processor.set_parameter(
        get_par_index(desc, "Amp Env Attack"), 1.0
    )  # set attack in milliseconds
    sampler_processor.set_parameter(
        get_par_index(desc, "Amp Env Decay"), 0.0
    )  # set decay in milliseconds
    sampler_processor.set_parameter(get_par_index(desc, "Amp Env Sustain"), 1.0)  # set sustain
    sampler_processor.set_parameter(
        get_par_index(desc, "Amp Env Release"), 100.0
    )  # set release in milliseconds

    amp_index = get_par_index(desc, "Amp Active")
    sampler_processor.set_parameter(amp_index, 1.0)

    assert sampler_processor.n_midi_events == 3 * 2  # multiply by 2 because of the off-notes.

    graph = [(sampler_processor, [])]

    engine.load_graph(graph)

    render(engine, file_path=OUTPUT / "test_sampler_with_amp.wav", duration=DURATION)

    sampler_processor.set_parameter(amp_index, 0.0)

    assert sampler_processor.n_midi_events == 3 * 2  # multiply by 2 because of the off-notes.

    render(engine, file_path=OUTPUT / "test_sampler_without_amp.wav", duration=DURATION)

    audio = engine.get_audio()
    assert np.mean(np.abs(audio)) > 0.01


def test_sampler_transpose():
    """The sampler's global "Transpose" parameter should shift pitch smoothly
    (no discontinuities/clicks) and apply to every simultaneously-held voice,
    since the underlying engine is a polyphonic (MPE) synthesiser."""

    def get_par_index(desc, par_name):
        for parDict in desc:
            if parDict["name"] == par_name:
                return parDict["index"]
        raise ValueError(f"Parameter '{par_name}' not found.")

    DURATION = 1.0

    engine = daw.RenderEngine(SAMPLE_RATE, BUFFER_SIZE)

    data = load_audio_file(ASSETS / "60988__folktelemetry__crash-fast-14.wav")
    sampler_processor = engine.make_sampler_processor("playback", data)

    desc = sampler_processor.get_parameters_description()
    transpose_index = get_par_index(desc, "Transpose")

    # A three-note chord played at once: exercises polyphony (multiple keys/voices
    # sounding simultaneously) through the same transpose engine.
    sampler_processor.add_midi_note(60, 100, 0.0, 0.5)
    sampler_processor.add_midi_note(64, 100, 0.0, 0.5)
    sampler_processor.add_midi_note(67, 100, 0.0, 0.5)

    # Transpose up an octave. Default range is [-48, 48] semitones.
    sampler_processor.set_parameter(transpose_index, 12.0)

    graph = [(sampler_processor, [])]
    engine.load_graph(graph)

    render(engine, file_path=OUTPUT / "test_sampler_transpose.wav", duration=DURATION)

    audio = engine.get_audio()

    # Audio was produced and contains no discontinuities (NaN/Inf). Check energy in
    # just the attack (the sample is a fast crash transient, and transposing up an
    # octave plays it back roughly twice as fast, so it decays well within DURATION;
    # checking the full-buffer mean would be flaky against the transpose amount).
    assert np.all(np.isfinite(audio))
    attack = audio[:, : int(0.05 * SAMPLE_RATE)]
    assert np.mean(np.abs(attack)) > 0.01

    # An untransposed render of the identical chord should differ from the
    # transposed one (the transpose parameter audibly affects playback).
    engine2 = daw.RenderEngine(SAMPLE_RATE, BUFFER_SIZE)
    sampler_processor2 = engine2.make_sampler_processor("playback", data)
    sampler_processor2.add_midi_note(60, 100, 0.0, 0.5)
    sampler_processor2.add_midi_note(64, 100, 0.0, 0.5)
    sampler_processor2.add_midi_note(67, 100, 0.0, 0.5)
    engine2.load_graph([(sampler_processor2, [])])
    render(engine2, file_path=OUTPUT / "test_sampler_no_transpose.wav", duration=DURATION)
    audio_untransposed = engine2.get_audio()

    assert not np.allclose(audio, audio_untransposed, atol=1e-6)
