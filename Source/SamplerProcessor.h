#pragma once

#include <filesystem>
#include <map>
#include <vector>

#include "../Source/Sampler/Source/SamplerAudioProcessor.h"
#include "custom_nanobind_wrappers.h"
#include "MidiSerialization.h"
#include "ProcessorBase.h"

class SamplerProcessor : public ProcessorBase
{
  public:
    SamplerProcessor(std::string newUniqueName, std::vector<std::vector<float>> inputData,
                     double sr, int blocksize)
        : ProcessorBase{newUniqueName}, mySampleRate{sr}
    {
        // Store original data before upsampling
        myOriginalSampleData = inputData;

        createParameterLayout();
        sampler.setNonRealtime(true);
        sampler.setSample(inputData, mySampleRate);
        setMainBusInputsAndOutputs(0, inputData.size());
    }

    SamplerProcessor(std::string newUniqueName, nb::ndarray<float> input, double sr, int blocksize)
        : ProcessorBase{newUniqueName}, mySampleRate{sr}
    {
        createParameterLayout();
        sampler.setNonRealtime(true);
        setData(input);
        setMainBusInputsAndOutputs(0, (int)input.shape(0));
    }

    ~SamplerProcessor()
    {
        myMidiBufferQN.clear();
        myMidiBufferSec.clear();
        myRenderMidiBuffer.clear();
        myRecordedMidiSequence.clear();
        delete myMidiIteratorSec;
        delete myMidiIteratorQN;
    }

    bool acceptsMidi() const override { return true; }
    bool producesMidi() const override { return true; }

    void setPlayHead(AudioPlayHead* newPlayHead) override
    {
        AudioProcessor::setPlayHead(newPlayHead);
        sampler.setPlayHead(newPlayHead);
    }

    void prepareToPlay(double sampleRate, int samplesPerBlock) override
    {
        sampler.prepareToPlay(sampleRate, samplesPerBlock);
    }

    void reset() override
    {
        sampler.reset();

        delete myMidiIteratorSec;
        myMidiIteratorSec = new MidiBuffer::Iterator(myMidiBufferSec); // todo: deprecated.

        myMidiEventsDoRemainSec =
            myMidiIteratorSec->getNextEvent(myMidiMessageSec, myMidiMessagePositionSec);

        delete myMidiIteratorQN;
        myMidiIteratorQN = new MidiBuffer::Iterator(myMidiBufferQN); // todo: deprecated.

        myMidiEventsDoRemainQN =
            myMidiIteratorQN->getNextEvent(myMidiMessageQN, myMidiMessagePositionQN);

        myRenderMidiBuffer.clear();
        myPendingTransposeShifts.clear();

        myRecordedMidiSequence.clear();
        myRecordedMidiSequence.addEvent(juce::MidiMessage::midiStart());
        myRecordedMidiSequence.addEvent(juce::MidiMessage::timeSignatureMetaEvent(4, 4));
        myRecordedMidiSequence.addEvent(juce::MidiMessage::tempoMetaEvent(500 * 1000));
        myRecordedMidiSequence.addEvent(juce::MidiMessage::midiChannelMetaEvent(1));
        ProcessorBase::reset();
    }

    void processBlock(juce::AudioSampleBuffer& buffer, juce::MidiBuffer& midiBuffer) override
    {
        auto posInfo = getPlayHead()->getPosition();

        buffer.clear(); // todo: why did this become necessary?
        midiBuffer.clear();
        myRenderMidiBuffer.clear();

        {
            auto start = *posInfo->getTimeInSamples();
            auto end = start + buffer.getNumSamples();

            myIsMessageBetweenSec =
                myMidiMessagePositionSec >= start && myMidiMessagePositionSec < end;
            while (myIsMessageBetweenSec && myMidiEventsDoRemainSec)
            {
                // steps for saving midi to file output
                auto messageCopy = MidiMessage(myMidiMessageSec);
                messageCopy.setTimeStamp(myMidiMessagePositionSec * (2400. / mySampleRate));
                if (!(messageCopy.isEndOfTrackMetaEvent() || messageCopy.isTempoMetaEvent()))
                {
                    myRecordedMidiSequence.addEvent(messageCopy);
                }

                // steps for playing MIDI
                myRenderMidiBuffer.addEvent(myMidiMessageSec,
                                            int(myMidiMessagePositionSec - start));
                myMidiEventsDoRemainSec =
                    myMidiIteratorSec->getNextEvent(myMidiMessageSec, myMidiMessagePositionSec);
                myIsMessageBetweenSec =
                    myMidiMessagePositionSec >= start && myMidiMessagePositionSec < end;
            }
        }

        {
            auto pulseStart = std::floor(*posInfo->getPpqPosition() * PPQN);
            auto pulseEnd = pulseStart + buffer.getNumSamples() * (*posInfo->getBpm() * PPQN) /
                                             (mySampleRate * 60.);

            myIsMessageBetweenQN =
                myMidiMessagePositionQN >= pulseStart && myMidiMessagePositionQN < pulseEnd;
            while (myIsMessageBetweenQN && myMidiEventsDoRemainQN)
            {
                // steps for saving midi to file output
                auto messageCopy = MidiMessage(myMidiMessageQN);
                messageCopy.setTimeStamp(
                    (*posInfo->getTimeInSeconds() +
                     (myMidiMessagePositionQN - pulseStart) * (60. / (*posInfo->getBpm()) / PPQN)) *
                    2400.);
                if (!(messageCopy.isEndOfTrackMetaEvent() || messageCopy.isTempoMetaEvent()))
                {
                    myRecordedMidiSequence.addEvent(messageCopy);
                }

                // steps for playing MIDI
                myRenderMidiBuffer.addEvent(myMidiMessageQN,
                                            int((myMidiMessagePositionQN - pulseStart) * 60. *
                                                mySampleRate / (PPQN * *posInfo->getBpm())));
                myMidiEventsDoRemainQN =
                    myMidiIteratorQN->getNextEvent(myMidiMessageQN, myMidiMessagePositionQN);
                myIsMessageBetweenQN =
                    myMidiMessagePositionQN >= pulseStart && myMidiMessagePositionQN < pulseEnd;
            }
        }

        applyTranspose();

        sampler.processBlock(buffer, myRenderMidiBuffer);

        ProcessorBase::processBlock(buffer, midiBuffer);
    }

    // A global, real-time-safe pitch transpose (in semitones) applied on top of every
    // note. This is "DT style": ultra-low-latency (it's just a note-number shift at
    // note-on time, so playback still runs through the sampler's normal zero-lookahead
    // resampling path, with no added buffering/algorithmic latency) and polyphonic
    // (every simultaneously-held key gets its own independently-tracked shift).
    //
    // The current transpose value is captured once, at each note-on, and remembered
    // until that note's matching note-off, rather than being re-read live. That keeps
    // a note in tune for its whole duration even if the transpose is automated/swept
    // while the note is still sounding, and guarantees the note-off always carries the
    // same note number as its note-on (otherwise the underlying synth wouldn't find a
    // matching held note, and the note would hang / never turn off).
    void applyTranspose()
    {
        int semitoneShift = juce::roundToInt(
            juce::jlimit(-kTransposeRangeSemitones, kTransposeRangeSemitones, myTransposeSemitones));

        if (semitoneShift == 0 && myPendingTransposeShifts.empty())
            return;

        juce::MidiBuffer transposedBuffer;

        juce::MidiBuffer::Iterator it(myRenderMidiBuffer);
        MidiMessage message;
        int samplePosition;

        while (it.getNextEvent(message, samplePosition))
        {
            if (message.isNoteOn())
            {
                auto key = noteKey(message.getChannel(), message.getNoteNumber());
                myPendingTransposeShifts[key].push_back(semitoneShift);

                int newNote = juce::jlimit(0, 127, message.getNoteNumber() + semitoneShift);
                message = juce::MidiMessage::noteOn(message.getChannel(), newNote, message.getVelocity());
            }
            else if (message.isNoteOff())
            {
                int shift = 0;
                auto key = noteKey(message.getChannel(), message.getNoteNumber());
                auto found = myPendingTransposeShifts.find(key);
                if (found != myPendingTransposeShifts.end() && !found->second.empty())
                {
                    shift = found->second.front();
                    found->second.erase(found->second.begin());
                    if (found->second.empty())
                        myPendingTransposeShifts.erase(found);
                }

                int newNote = juce::jlimit(0, 127, message.getNoteNumber() + shift);
                message = juce::MidiMessage::noteOff(message.getChannel(), newNote, message.getVelocity());
            }

            transposedBuffer.addEvent(message, samplePosition);
        }

        myRenderMidiBuffer.swapWith(transposedBuffer);
    }

    static int noteKey(int channel, int noteNumber) { return (channel << 8) | noteNumber; }

    const juce::String getName() const override { return "SamplerProcessor"; }

    nb::ndarray<nb::numpy, float> getData()
    {
        // Return the original non-upsampled data for serialization
        if (myOriginalSampleData.empty())
        {
            // Return empty array if no sample loaded
            size_t shape[2] = {0, 0};
            return nb::ndarray<nb::numpy, float>(nullptr, 2, shape);
        }

        int num_channels = (int)myOriginalSampleData.size();
        int num_samples = (int)myOriginalSampleData[0].size();

        // Allocate output array
        size_t shape[2] = {(size_t)num_channels, (size_t)num_samples};
        float* array_data = new float[num_channels * num_samples];
        auto capsule =
            nb::capsule(array_data, [](void* p) noexcept { delete[] static_cast<float*>(p); });
        nb::ndarray<nb::numpy, float> output =
            nb::ndarray<nb::numpy, float>(array_data, 2, shape, capsule);

        // Copy data from original sample data
        for (int chan = 0; chan < num_channels; chan++)
        {
            for (int sample = 0; sample < num_samples; sample++)
            {
                array_data[chan * num_samples + sample] = myOriginalSampleData[chan][sample];
            }
        }

        return output;
    }

    void setData(nb::ndarray<float> input)
    {
        float* input_ptr = (float*)input.data();

        int num_channels = (int)input.shape(0);
        int num_samples = (int)input.shape(1);

        std::vector<std::vector<float>> data =
            std::vector<std::vector<float>>(num_channels, std::vector<float>(num_samples));

        // Get strides - nanobind returns ELEMENT strides, not byte strides
        size_t elem_stride_ch = input.stride(0);     // stride for channel dimension (in elements)
        size_t elem_stride_sample = input.stride(1); // stride for sample dimension (in elements)

        // Copy data using strides (handles both C-contiguous and F-contiguous)
        for (int chan = 0; chan < num_channels; chan++)
        {
            float* chan_ptr = input_ptr + (chan * elem_stride_ch);
            for (int samp = 0; samp < num_samples; samp++)
            {
                data[chan][samp] = chan_ptr[samp * elem_stride_sample];
            }
        }

        // Store original data before upsampling
        myOriginalSampleData = data;

        sampler.setSample(data, mySampleRate);
    }

    int getNumMidiEvents()
    {
        return myMidiBufferSec.getNumEvents() + myMidiBufferQN.getNumEvents();
    };

    bool loadMidi(const std::string& path, bool clearPrevious, bool isBeats, bool allEvents)
    {
        if (!std::filesystem::exists(path.c_str()))
        {
            throw std::runtime_error("File not found: " + path);
        }

        File file = File(path);
        FileInputStream fileStream(file);
        MidiFile midiFile;
        midiFile.readFrom(fileStream);

        if (clearPrevious)
        {
            myMidiBufferSec.clear();
            myMidiBufferQN.clear();
        }

        if (!isBeats)
        {
            midiFile.convertTimestampTicksToSeconds();

            for (int t = 0; t < midiFile.getNumTracks(); t++)
            {
                const MidiMessageSequence* track = midiFile.getTrack(t);
                for (int i = 0; i < track->getNumEvents(); i++)
                {
                    MidiMessage& m = track->getEventPointer(i)->message;
                    int sampleOffset = (int)(mySampleRate * m.getTimeStamp());
                    if (allEvents || m.isNoteOff() || m.isNoteOn())
                    {
                        myMidiBufferSec.addEvent(m, sampleOffset);
                    }
                }
            }
        }
        else
        {
            auto timeFormat = midiFile.getTimeFormat(); // the ppqn (Ableton makes
                                                        // midi files with 96 ppqn)
            for (int t = 0; t < midiFile.getNumTracks(); t++)
            {
                const MidiMessageSequence* track = midiFile.getTrack(t);
                for (int i = 0; i < track->getNumEvents(); i++)
                {
                    MidiMessage& m = track->getEventPointer(i)->message;

                    if (allEvents || m.isNoteOff() || m.isNoteOn())
                    {
                        // convert timestamp from its original time format to our high
                        // resolution PPQN
                        auto timeStamp = m.getTimeStamp() * PPQN / timeFormat;
                        myMidiBufferQN.addEvent(m, timeStamp);
                    }
                }
            }
        }

        return true;
    }

    void clearMidi()
    {
        myMidiBufferSec.clear();
        myMidiBufferQN.clear();
    }

    bool addMidiNote(uint8 midiNote, uint8 midiVelocity, const double noteStart,
                     const double noteLength, bool isBeats)
    {
        if (midiNote > 255)
            midiNote = 255;
        if (midiNote < 0)
            midiNote = 0;
        if (midiVelocity > 255)
            midiVelocity = 255;
        if (midiVelocity < 0)
            midiVelocity = 0;
        if (noteLength <= 0)
        {
            throw std::runtime_error("The note length must be greater than zero.");
        }

        // Get the note on midiBuffer.
        MidiMessage onMessage = MidiMessage::noteOn(1, midiNote, midiVelocity);

        MidiMessage offMessage = MidiMessage::noteOff(1, midiNote, midiVelocity);

        if (!isBeats)
        {
            auto startTime = noteStart * mySampleRate;
            onMessage.setTimeStamp(startTime);
            offMessage.setTimeStamp(startTime + noteLength * mySampleRate);
            myMidiBufferSec.addEvent(onMessage, (int)onMessage.getTimeStamp());
            myMidiBufferSec.addEvent(offMessage, (int)offMessage.getTimeStamp());
        }
        else
        {
            auto startTime = noteStart * PPQN;
            onMessage.setTimeStamp(startTime);
            offMessage.setTimeStamp(startTime + noteLength * PPQN);
            myMidiBufferQN.addEvent(onMessage, (int)onMessage.getTimeStamp());
            myMidiBufferQN.addEvent(offMessage, (int)offMessage.getTimeStamp());
        }

        return true;
    }

    void saveMIDI(std::string& savePath)
    {
        MidiFile file;

        // 30*80 = 2400, so that's why the MIDI messages had their
        // timestamp set to seconds*2400
        file.setSmpteTimeFormat(30, 80);

        File myFile(savePath);

        file.addTrack(myRecordedMidiSequence);

        juce::FileOutputStream stream(myFile);
        if (stream.openedOk())
        {
            // overwrite existing file.
            stream.setPosition(0);
            stream.truncate();
        }
        file.writeTo(stream);
    }

    std::string wrapperGetParameterName(int parameter)
    {
        if (parameter == sampler.getNumParameters())
            return "Transpose";
        return sampler.getParameterName(parameter).toStdString();
    }

    std::string wrapperGetParameterAsText(const int parameter)
    {
        if (parameter == sampler.getNumParameters())
            return juce::String(myTransposeSemitones, 2).toStdString();
        return sampler.getParameterText(parameter).toStdString();
    }

    int wrapperGetPluginParameterSize() { return sampler.getNumParameters() + 1; }

    nb::list getParametersDescription()
    {
        nb::list myList;

        // get the parameters as an AudioProcessorParameter array
        const Array<AudioProcessorParameter*>& processorParams = sampler.getParameters();
        for (int i = 0; i < sampler.getNumParameters(); i++)
        {
            int maximumStringLength = 64;

            std::string theName = (processorParams[i])->getName(maximumStringLength).toStdString();
            std::string currentText =
                processorParams[i]
                    ->getText(processorParams[i]->getValue(), maximumStringLength)
                    .toStdString();
            std::string label = processorParams[i]->getLabel().toStdString();

            nb::dict myDictionary;
            myDictionary["index"] = i;
            myDictionary["name"] = theName;
            myDictionary["numSteps"] = processorParams[i]->getNumSteps();
            myDictionary["isDiscrete"] = processorParams[i]->isDiscrete();
            myDictionary["label"] = label;
            myDictionary["text"] = currentText;

            myList.append(myDictionary);
        }

        {
            // Global transpose, in semitones. Lives in this wrapper (not the underlying
            // sampler's own parameter tree), since it's implemented as a note-number
            // shift applied to MIDI events on their way into the sampler.
            int i = sampler.getNumParameters();

            nb::dict myDictionary;
            myDictionary["index"] = i;
            myDictionary["name"] = std::string("Transpose");
            myDictionary["numSteps"] = 0;
            myDictionary["isDiscrete"] = false;
            myDictionary["label"] = std::string("st");
            myDictionary["text"] = wrapperGetParameterAsText(i);

            myList.append(myDictionary);
        }

        return myList;
    }

    void createParameterLayout()
    {
        juce::AudioProcessorParameterGroup group;

        for (int i = 0; i < sampler.getNumParameters(); ++i)
        {
            auto parameterName = sampler.getParameterName(i);
            group.addChild(std::make_unique<AutomateParameterFloat>(
                parameterName, parameterName, NormalisableRange<float>(0.f, 1.f), 0.f));
        }

        // Extra parameter, appended after all of the underlying sampler's own
        // parameters, so existing parameter indices are unaffected.
        group.addChild(std::make_unique<AutomateParameterFloat>(
            "Transpose", "Transpose",
            NormalisableRange<float>(-kTransposeRangeSemitones, kTransposeRangeSemitones), 0.f));

        this->setParameterTree(std::move(group));

        for (int i = 0; i < sampler.getNumParameters(); ++i)
        {
            // give it a valid single sample of automation.
            ProcessorBase::setAutomationValByIndex(i, sampler.getParameter(i));
        }
        ProcessorBase::setAutomationValByIndex(sampler.getNumParameters(), 0.f);
    }

    void automateParameters(AudioPlayHead::PositionInfo& posInfo, int numSamples) override
    {
        auto allParameters = this->getParameters();

        for (int i = 0; i < sampler.getNumParameters(); i++)
        {
            auto theParameter = (AutomateParameterFloat*)allParameters.getUnchecked(i);
            sampler.setParameterRawNotifyingHost(i, theParameter->sample(posInfo));
        }

        auto transposeParameter =
            (AutomateParameterFloat*)allParameters.getUnchecked(sampler.getNumParameters());
        myTransposeSemitones = transposeParameter->sample(posInfo);
    }

    nb::dict getPickleState()
    {
        nb::dict state;
        state["unique_name"] = getUniqueName();
        state["sample_rate"] = mySampleRate;

        // Get sample data
        state["sample_data"] = getData();

        // Get all parameter values (including the extra Transpose parameter appended
        // after the sampler's own parameters).
        nb::list params;
        for (int i = 0; i < sampler.getNumParameters() + 1; i++)
        {
            params.append(getAutomationAtZeroByIndex(i));
        }
        state["parameters"] = params;

        // Serialize MIDI buffers using shared helper
        state["midi_qn"] = MidiSerialization::serializeMidiBuffer(myMidiBufferQN);
        state["midi_sec"] = MidiSerialization::serializeMidiBuffer(myMidiBufferSec);

        return state;
    }

    void setPickleState(nb::dict state)
    {
        std::string name = nb::cast<std::string>(state["unique_name"]);
        double sr = nb::cast<double>(state["sample_rate"]);
        nb::ndarray<float> sample_data = nb::cast<nb::ndarray<float>>(state["sample_data"]);

        // Reconstruct using placement new
        new (this) SamplerProcessor(name, sample_data, sr, 512);

        // Restore parameters
        if (state.contains("parameters"))
        {
            nb::list params = nb::cast<nb::list>(state["parameters"]);
            for (int i = 0; i < nb::len(params); i++)
            {
                float value = nb::cast<float>(params[i]);
                setAutomationValByIndex(i, value);
            }
        }

        // Restore MIDI buffers using shared helper
        if (state.contains("midi_qn"))
        {
            nb::bytes midi_qn_data = nb::cast<nb::bytes>(state["midi_qn"]);
            MidiSerialization::deserializeMidiBuffer(myMidiBufferQN, midi_qn_data);
        }

        if (state.contains("midi_sec"))
        {
            nb::bytes midi_sec_data = nb::cast<nb::bytes>(state["midi_sec"]);
            MidiSerialization::deserializeMidiBuffer(myMidiBufferSec, midi_sec_data);
        }
    }

  private:
    double mySampleRate;

    SamplerAudioProcessor sampler;

    // Store original non-upsampled sample data for serialization
    std::vector<std::vector<float>> myOriginalSampleData;

    MidiBuffer myMidiBufferQN;
    MidiBuffer myMidiBufferSec;

    MidiBuffer myRenderMidiBuffer;

    MidiMessage myMidiMessageQN;
    MidiMessage myMidiMessageSec;

    int myMidiMessagePositionQN = -1;
    int myMidiMessagePositionSec = -1;

    MidiBuffer::Iterator* myMidiIteratorQN = nullptr;
    MidiBuffer::Iterator* myMidiIteratorSec = nullptr;

    bool myIsMessageBetweenQN = false;
    bool myIsMessageBetweenSec = false;

    bool myMidiEventsDoRemainQN = false;
    bool myMidiEventsDoRemainSec = false;

    MidiMessageSequence myRecordedMidiSequence;

    static constexpr float kTransposeRangeSemitones = 48.f;
    float myTransposeSemitones = 0.f;

    // key = (midiChannel << 8) | noteNumber -> FIFO of semitone shifts applied to
    // still-held note-on events, so each note-off can be shifted to match.
    std::map<int, std::vector<int>> myPendingTransposeShifts;
};
