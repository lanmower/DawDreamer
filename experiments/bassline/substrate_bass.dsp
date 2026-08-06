declare name "SubstrateBass";
declare options "[nvoices:1]";
import("stdfaust.lib");

freq = hslider("freq", 110, 20, 1000, 0.01);
gain = hslider("gain", 0.85, 0, 1, 0.001);
gate = button("gate");

subMix   = hslider("subMix", 0.46, 0, 1, 0.001);
sqMix    = hslider("sqMix", 0.12, 0, 1, 0.001);
cutoffHz = hslider("cutoff", 500, 40, 15000, 1);
resoQ    = hslider("reso", 4, 0.707, 18, 0.01);
envAmt   = hslider("envAmt", 5, 0, 14, 0.01);
decayT   = hslider("decay", 0.18, 0.01, 1.5, 0.001);
driveAmt = hslider("drive", 0.18, 0, 1, 0.001);

osc1 = os.sawtooth(freq);
osc2 = os.osc(freq/2);
osc3 = os.square(freq);

voice = osc1*(1-subMix*0.55) + osc2*subMix + osc3*sqMix;

ampEnv  = en.adsr(0.002, decayT*0.6, 0.5, 0.06, gate);
filtEnv = en.adsr(0.0012, decayT, 0.0, 0.05, gate);

driveGain = 1 + driveAmt*7;
driven = ma.tanh(voice * driveGain) / ma.tanh(driveGain);

cutoffMod = min(cutoffHz * (1 + envAmt*filtEnv), 16000);

process = driven * gain * ampEnv : fi.resonlp(cutoffMod, resoQ, 1) <: _, _;

effect = _, _;
