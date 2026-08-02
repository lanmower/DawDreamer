declare name "DT_Whammy";
declare description "Ultra-low-latency, glitch-free Whammy/harmonizer pitch shifter (DigiTech Whammy style). With num_voices == 0 it behaves like the real pedal: one continuous mono-in/stereo-out transpose effect driven by an expression pedal and a mode dial. With num_voices > 0 it becomes a polyphonic MIDI-keyboard harmonizer: turn harmony_mode on and each held key transposes the same input signal to that key's interval above/below root_note, and the voices sum into chords.";
declare author "DawDreamer";
declare license "MIT";
declare options "[nvoices:8]"; // FaustProcessor.num_voices overrides this at runtime.

import("stdfaust.lib");

//=====================================================================================
// Controls
//=====================================================================================

// Master bypass and wet level. Only meaningful in the mono pedal path (harmony_mode off):
// in harmony mode there is no per-voice "dry" signal to blend (see voice logic below), so
// bypass mutes the voice and mix scales its harmony level instead of crossfading dry/wet.
bypass = checkbox("[0]bypass");
mix    = hslider("[1]mix", 1, 0, 1, 0.001) : si.smoo;

// "Expression pedal": 0 = heel (no shift), 1 = toe (full shift for the selected mode).
// Smoothed so sweeping the pedal never zippers, exactly like riding a real Whammy pedal.
pedal = hslider("[2]pedal[style:knob]", 0, 0, 1, 0.001) : si.smoo;

// Classic DigiTech Whammy interval presets, reached at pedal = 1.
mode = nentry(
    "[3]mode[style:menu{'Octave Up':0;'Octave Down':1;'2 Octaves Up':2;'2 Octaves Down':3;'Fifth Up':4;'Fourth Up':5;'Fifth Down':6;'Dive Bomb':7}]",
    0, 0, 7, 1);

// Polyphonic harmonizer mode: when enabled, held MIDI keys set the transpose interval
// (relative to root_note) instead of the pedal alone, turning the keyboard into a chord
// harmonizer for the incoming audio signal. The pedal still rides on top of every voice,
// so you can dive/bend a whole held chord.
harmony_mode = checkbox("[4]harmony_mode");
root_note    = hslider("[5]root_note[unit:MIDI][tooltip: key that produces no pitch shift]", 60, 0, 127, 1);

// Glide: portamento time for pitch-shift changes (key changes / pedal moves). This is the
// main control for "extra smooth" - it removes clicks and zipper noise on every transpose
// change, including polyphonic note-to-note glides.
glide_ms = hslider("[6]glide_ms[style:knob][unit:ms]", 12, 0.1, 250, 0.1);

// Shifter engine tuning: smaller window = lower latency, larger crossfade = smoother tone.
window_ms = hslider("[7]window_ms[style:knob][unit:ms][tooltip: pitch-shifter window length, smaller = lower latency]", 15, 5, 40, 0.1);
xfade_pct = hslider("[8]crossfade_pct[style:knob][unit:%][tooltip: percent of the window used to crossfade the two delay taps]", 50, 10, 90, 1);

// Per-voice MIDI controls (Faust polyphony convention: these exact names are bound to
// incoming MIDI note/velocity/gate when num_voices > 0; in mono mode they stay at their
// defaults unless driven manually via set_parameter).
freq = hslider("freq", 261.6255653005986, 20, 20000, 0.001); // note pitch in Hz
gain = hslider("gain", 1, 0, 1, 0.01);                       // note velocity
gate = button("gate");                                       // note on/off

//=====================================================================================
// Derived control-rate signals
//=====================================================================================

modeShift = (12, -12, 24, -24, 7, 5, -7, -24) : ba.selectn(8, int(mode));

winSamples = int(window_ms * 0.001 * ma.SR) : max(64);
xfSamples  = int(winSamples * xfade_pct / 100.0) : max(32);

pedalShift   = pedal * modeShift;
harmonyShift = ba.hz2midikey(max(1, freq)) - root_note;

// In harmony mode the key sets the interval and the pedal rides on top of it; otherwise
// the pedal alone drives the classic mono whammy sweep.
targetShift = ba.if(harmony_mode, harmonyShift + pedalShift, pedalShift);

// Smoothed (glided) shift amount in semitones - kills zipper noise on every change.
shiftAmount = targetShift : si.smooth(ba.tau2pole(glide_ms * 0.001));

// Fast, click-free attack/release envelope so poly note on/off is glitch-free too. Only
// applied in harmony mode: the mono pedal path runs continuously (no note to gate on).
voiceEnv   = en.adsr(0.003, 0.03, 1, 0.05, gate);
voiceLevel = ba.if(harmony_mode, gain * voiceEnv, 1);

//=====================================================================================
// Pitch-shifting core: a 2-tap crossfaded delay-line shifter (ef.transpose from the
// Faust standard library). This is the low-latency, smooth whammy engine.
//=====================================================================================

whammyCore(sig) = (sig : ef.transpose(winSamples, xfSamples, shiftAmount)) * voiceLevel;

//=====================================================================================
// process: stereo in, stereo out (both channels run through their own shift-engine
// instance, driven by the same control-rate parameters so the stereo image stays
// coherent). Runs once directly when num_voices == 0 (classic mono pedal, full dry/wet
// + bypass against the real input), or once per active MIDI voice when num_voices > 0
// (poly engine sums the voices into a chord).
//=====================================================================================

process(sigL, sigR) = outL, outR
with {
    // No per-voice dry signal in harmony mode - avoids the dry line being summed once
    // per held key. Hold the root_note key itself (0 semitone shift) to add the
    // un-transposed input back into a chord.
    dryL = ba.if(harmony_mode, 0, sigL);
    dryR = ba.if(harmony_mode, 0, sigR);
    wetL = whammyCore(sigL);
    wetR = whammyCore(sigR);
    outL = ba.if(bypass, dryL, dryL * (1 - mix) + wetL * mix);
    outR = ba.if(bypass, dryR, dryR * (1 - mix) + wetR * mix);
};

// Polyphonic DSP code must declare a stereo effect, applied once after voices are
// summed; bypass/mix/envelope are already handled per-voice above, so this stays flat.
effect = _, _;
