// MP3 and AAC decoded in-process, exactly as on Android through MediaCodec.
//
// The decoders are Android's own software decoders (AOSP android-15.0.0_r1): the PacketVideo
// MP3 decoder behind c2.android.mp3.decoder and the Fraunhofer FDK AAC decoder behind
// c2.android.aac.decoder. Each is driven as its Codec2 wrapper drives it (C2SoftMp3Dec.cpp,
// C2SoftAacDec.cpp: one work per packet, the same delay trimming, silence for bad frames,
// DRC settings, end-of-stream handling), and the decoded buffers go through PcmSink, which
// does what the app's MediaCodec loop (AudioDecoder.kt) does with them. So the PCM is the
// same as with MediaExtractor + MediaCodec, without a round trip to the media process per
// packet.

#include <algorithm>
#include <cstring>
#include <deque>
#include <memory>
#include <numeric>
#include <string>

#include "chordchart/audio.hpp"
#include "decode_internal.hpp"

// the decoders' headers last: they define macros (pvmp3: `module`) that clash with the
// standard library's
#include <aacdecoder_lib.h>
#include <pvmp3decoder_api.h>

#include "DrcPresModeWrap.h"
#undef module

namespace chordchart {

namespace {

// What AudioDecoder.kt does with a decoder's output: a new resampler at every output format
// change (which drops what the old one held), else one from the track's format at the first
// non-empty buffer. A change after audio was fed would lose that audio there; rather than
// copy that, the file goes back to the platform decoder (UnsupportedContainer), which gives
// the app's old result by definition.
//
// Before the app sees them, MediaCodec (CCodecBufferChannel) also drops the encoder delay
// and padding the extractor found (gapless playback: LAME's tag, an MP4 edit list,
// iTunSMPB) with a SkipCutBuffer: the first `delay` frames, and the last `padding` frames,
// which it holds back and never releases. Mirrored here, buffer by buffer.
class PcmSink {
public:
    explicit PcmSink(const AudioTrack& track) : track_rate_(track.sample_rate), track_channels_(track.channels) {
        // OutputBuffers::initSkipCutBuffer, with the configured (track) format
        if (track.encoder_delay || track.encoder_padding) {
            delay_ = track.encoder_delay;
            padding_ = track.encoder_padding;
            skip_rate_ = track_rate_;
            skip_channels_ = track_channels_;
            set_skip_cut(delay_, padding_);
        }
    }

    void format_changed(int rate, int channels) {
        if (fed_) throw UnsupportedContainer("the output format changed mid-stream");
        // OutputBuffers::updateSkipCutBuffer
        if (skip_cut_ && (rate != skip_rate_ || channels != skip_channels_)) {
            int32_t delay = delay_, padding = padding_;
            if (rate != skip_rate_) {
                delay = static_cast<int32_t>(static_cast<int64_t>(delay) * rate / skip_rate_);
                padding = static_cast<int32_t>(static_cast<int64_t>(padding) * rate / skip_rate_);
            }
            skip_rate_ = rate;
            skip_channels_ = channels;
            set_skip_cut(delay, padding);
        }
        resampler_ = std::make_unique<MonoResampler>(rate, channels);
    }
    void buffer(const int16_t* samples, size_t count) {
        if (skip_cut_) {
            // SkipCutBuffer::submit: drop what's left of the front, queue the rest, release
            // all but the last `cut` samples
            const size_t drop = std::min(count, front_left_);
            front_left_ -= drop;
            held_.insert(held_.end(), samples + drop, samples + count);
            if (held_.size() <= cut_) return;
            released_.assign(held_.begin(), held_.end() - static_cast<std::ptrdiff_t>(cut_));
            held_.erase(held_.begin(), held_.end() - static_cast<std::ptrdiff_t>(cut_));
            samples = released_.data();
            count = released_.size();
        }
        if (count == 0) return;  // empty buffers are skipped
        if (!resampler_) resampler_ = std::make_unique<MonoResampler>(track_rate_, track_channels_);
        resampler_->feed(samples, count);
        fed_ = true;
    }
    std::vector<int16_t> finish() {
        if (!resampler_) throw UnsupportedContainer("no audio samples");
        return resampler_->finish();
    }

private:
    int track_rate_, track_channels_;
    std::unique_ptr<MonoResampler> resampler_;
    bool fed_ = false;
    // the skip/cut stage
    bool skip_cut_ = false;
    int32_t delay_ = 0, padding_ = 0;
    int skip_rate_ = 0, skip_channels_ = 0;
    size_t front_left_ = 0, cut_ = 0;  // in samples (frames x channels)
    std::vector<int16_t> held_, released_;

    // OutputBuffers::setSkipCutBuffer: a new SkipCutBuffer (what the old one held is lost)
    void set_skip_cut(int32_t skip, int32_t cut) {
        held_.clear();
        if (skip_channels_ <= 0 || skip < 0 || cut < 0) {  // passthrough
            skip_cut_ = false;
            return;
        }
        skip_cut_ = true;
        front_left_ = static_cast<size_t>(skip) * static_cast<size_t>(skip_channels_);
        cut_ = static_cast<size_t>(cut) * static_cast<size_t>(skip_channels_);
    }
};

class Progress {
public:
    Progress(const Demuxer& demuxer, const std::function<void(double)>& report) : demuxer_(demuxer), report_(report) {}
    void update() {
        if (!report_ || !demuxer_.size()) return;
        const double f = static_cast<double>(demuxer_.position()) / static_cast<double>(demuxer_.size());
        if (f - reported_ >= 0.01) {
            reported_ = f;
            report_(f);
        }
    }
    void done() {
        if (report_) report_(1.0);
    }

private:
    const Demuxer& demuxer_;
    const std::function<void(double)>& report_;
    double reported_ = -1;
};

uint32_t u32(const uint8_t* p) { return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3]; }

// ---------------------------------------------------------------- MP3 (C2SoftMp3Dec.cpp)

constexpr int kPVMP3DecoderDelay = 529;  // samples

class Mp3Decoder {
public:
    Mp3Decoder() : mem_(pvmp3_decoderMemRequirements()) {
        config_.equalizerType = flat;
        config_.crcEnabled = false;
        pvmp3_InitDecoder(&config_, mem_.data());
    }

    // C2SoftMP3::process for one work: a packet, or (eos) the empty end-of-stream buffer.
    void process(const uint8_t* data, size_t in_size, bool eos, PcmSink& sink) {
        if (in_size == 0 && (!gapless_bytes_ || !eos)) return;
        int32_t num_channels = config_.num_channels;
        std::vector<size_t> decoded_sizes;  // bytes
        if (in_size) calculate_out_size(data, in_size, decoded_sizes);
        size_t cal_out_size = std::accumulate(decoded_sizes.begin(), decoded_sizes.end(), size_t{0});
        if (eos) cal_out_size += kPVMP3DecoderDelay * static_cast<size_t>(num_channels) * sizeof(int16_t);
        out_.assign(cal_out_size / sizeof(int16_t), 0);

        size_t out_size = 0, out_offset = 0;  // bytes
        auto it = decoded_sizes.begin();
        size_t in_pos = 0;
        int32_t sampling_rate = config_.samplingRate;
        bool format_update = false;
        while (in_pos < in_size) {
            if (it == decoded_sizes.end()) break;  // unexpected trailing bytes, ignored
            config_.pInputBuffer = const_cast<uint8_t*>(data + in_pos);
            config_.inputBufferCurrentLength = static_cast<int32_t>(in_size - in_pos);
            config_.inputBufferMaxLength = 0;
            config_.inputBufferUsedLength = 0;
            config_.outputFrameSize = static_cast<int32_t>((cal_out_size - out_size) / sizeof(int16_t));
            config_.pOutputBuffer = out_.data() + out_size / sizeof(int16_t);
            const ERROR_CODE err = pvmp3_framedecoder(&config_, mem_.data());
            if (err != NO_DECODING_ERROR) {
                if (err != NO_ENOUGH_MAIN_DATA_ERROR && err != SIDE_INFO_ERROR)
                    throw UnsupportedContainer("MP3 decoder error " + std::to_string(static_cast<int>(err)));
                // recoverable: silence for this frame
                if (config_.outputFrameSize == 0) config_.outputFrameSize = static_cast<int32_t>(*it / sizeof(int16_t));
                std::memset(config_.pOutputBuffer, 0, static_cast<size_t>(config_.outputFrameSize) * sizeof(int16_t));
            } else if (config_.samplingRate != sampling_rate || config_.num_channels != num_channels) {
                sampling_rate = config_.samplingRate;
                num_channels = config_.num_channels;
                format_update = true;
            }
            if (*it != static_cast<size_t>(config_.outputFrameSize) * sizeof(int16_t))
                throw UnsupportedContainer("MP3: parsed size does not match decoded size");
            out_size += static_cast<size_t>(config_.outputFrameSize) * sizeof(int16_t);
            in_pos += static_cast<size_t>(config_.inputBufferUsedLength);
            ++it;
        }
        if (is_first_) {
            is_first_ = false;
            gapless_bytes_ = true;
            // the decoder delay: trimmed off the start of the first output buffer
            out_offset = kPVMP3DecoderDelay * static_cast<size_t>(num_channels) * sizeof(int16_t);
        }
        if (eos) {
            const size_t tail = kPVMP3DecoderDelay * static_cast<size_t>(num_channels) * sizeof(int16_t);
            if (cal_out_size >= out_size + tail) {  // 529 samples of silence at the end
                std::memset(out_.data() + out_size / sizeof(int16_t), 0, tail);
                gapless_bytes_ = false;
                out_size += tail;
            }
        }
        if (format_update) sink.format_changed(sampling_rate, num_channels);
        if (sampling_rate && num_channels && out_size > out_offset)
            sink.buffer(out_.data() + out_offset / sizeof(int16_t), (out_size - out_offset) / sizeof(int16_t));
    }

private:
    tPVMP3DecoderExternal config_{};
    std::vector<uint8_t> mem_;
    std::vector<int16_t> out_;
    bool is_first_ = true, gapless_bytes_ = false;

    static void calculate_out_size(const uint8_t* header, size_t in_size, std::vector<size_t>& decoded_sizes) {
        size_t total = 0;
        while (total + 4 < in_size) {
            size_t frame_size;
            int channels, num_samples;
            if (!mpeg_audio_frame_size(u32(header + total), &frame_size, nullptr, &channels, nullptr, &num_samples))
                throw UnsupportedContainer("MP3: bad frame header");
            total += frame_size;
            decoded_sizes.push_back(static_cast<size_t>(num_samples) * static_cast<size_t>(channels) * sizeof(int16_t));
        }
        if (decoded_sizes.empty()) throw UnsupportedContainer("MP3: no frame in packet");
    }
};

// ---------------------------------------------------------------- AAC (C2SoftAacDec.cpp)

constexpr int kMaxChannelCount = 8;
constexpr int kNumDelayBlocksMax = 8;
constexpr int kFileReadMaxLayers = 2;

// The Codec2 parameters' defaults on a phone without the aac_drc_* overrides (as the
// emulator): C2SoftAacDec::IntfImpl and its getters.
constexpr int kDrcTargetRefLevel = 64;       // -16 dB
constexpr int kDrcEncTargetLevel = -1;       // unknown
constexpr int kDrcBoostFactor = 127;
constexpr int kDrcAttenuationFactor = 127;
constexpr int kDrcCompressMode = 1;          // heavy
constexpr int kDrcEffectType = 3;            // limited playback range
constexpr int kDrcAlbumMode = 0;

class AacDecoder {
public:
    explicit AacDecoder(const std::vector<uint8_t>& csd0) {
        // initDecoder()
        decoder_ = aacDecoder_Open(TT_MP4_ADIF, 1);
        if (!decoder_) throw UnsupportedContainer("AAC decoder didn't open");
        info_ = aacDecoder_GetStreamInfo(decoder_);
        if (!info_) throw UnsupportedContainer("AAC decoder has no stream info");
        ring_.assign(static_cast<size_t>(2048) * kMaxChannelCount * kNumDelayBlocksMax, 0);
        drc_.setDecoderHandle(decoder_);
        drc_.submitStreamData(info_);
        drc_.setParam(DRC_PRES_MODE_WRAP_DESIRED_TARGET, static_cast<unsigned>(kDrcTargetRefLevel));
        drc_.setParam(DRC_PRES_MODE_WRAP_DESIRED_ATT_FACTOR, static_cast<unsigned>(kDrcAttenuationFactor));
        drc_.setParam(DRC_PRES_MODE_WRAP_DESIRED_BOOST_FACTOR, static_cast<unsigned>(kDrcBoostFactor));
        drc_.setParam(DRC_PRES_MODE_WRAP_DESIRED_HEAVY, static_cast<unsigned>(kDrcCompressMode));
        drc_.setParam(DRC_PRES_MODE_WRAP_ENCODER_TARGET, static_cast<unsigned>(kDrcEncTargetLevel));
        aacDecoder_SetParam(decoder_, AAC_UNIDRC_SET_EFFECT, kDrcEffectType);
        aacDecoder_SetParam(decoder_, AAC_UNIDRC_ALBUM_MODE, kDrcAlbumMode);
        aacDecoder_SetParam(decoder_, AAC_PCM_MAX_OUTPUT_CHANNELS, kMaxChannelCount);
        // the codec-config work (csd-0)
        if (csd0.empty()) throw UnsupportedContainer("AAC without csd-0");
        UCHAR* in[kFileReadMaxLayers] = {const_cast<UCHAR*>(csd0.data()), nullptr};
        UINT length[kFileReadMaxLayers] = {static_cast<UINT>(csd0.size()), 0};
        if (aacDecoder_ConfigRaw(decoder_, in, length) != AAC_DEC_OK) throw UnsupportedContainer("AAC: bad config");
    }
    ~AacDecoder() {
        if (decoder_) aacDecoder_Close(decoder_);
    }
    AacDecoder(const AacDecoder&) = delete;
    AacDecoder& operator=(const AacDecoder&) = delete;

    // C2SoftAacDec::process for one work (raw AAC, not ADTS).
    void process(const uint8_t* data, size_t size, bool eos, PcmSink& sink) {
        INT_PCM tmp[2048 * kMaxChannelCount];
        size_t offset = 0;
        Info in_info;
        while (size > 0) {
            UCHAR* in[kFileReadMaxLayers] = {const_cast<UCHAR*>(data + offset), nullptr};
            UINT in_length[kFileReadMaxLayers] = {static_cast<UINT>(size), 0};
            UINT bytes_valid[kFileReadMaxLayers] = {in_length[0], 0};
            const INT prev_rate = info_->sampleRate, prev_channels = info_->numChannels,
                      prev_loudness = info_->outputLoudness;
            aacDecoder_Fill(decoder_, in, in_length, bytes_valid);
            drc_.submitStreamData(info_);
            drc_.setParam(DRC_PRES_MODE_WRAP_DESIRED_TARGET, static_cast<unsigned>(kDrcTargetRefLevel));
            drc_.setParam(DRC_PRES_MODE_WRAP_DESIRED_ATT_FACTOR, static_cast<unsigned>(kDrcAttenuationFactor));
            drc_.setParam(DRC_PRES_MODE_WRAP_DESIRED_BOOST_FACTOR, static_cast<unsigned>(kDrcBoostFactor));
            drc_.setParam(DRC_PRES_MODE_WRAP_DESIRED_HEAVY, static_cast<unsigned>(kDrcCompressMode));
            drc_.setParam(DRC_PRES_MODE_WRAP_ENCODER_TARGET, static_cast<unsigned>(kDrcEncTargetLevel));
            aacDecoder_SetParam(decoder_, AAC_UNIDRC_SET_EFFECT, kDrcEffectType);
            aacDecoder_SetParam(decoder_, AAC_UNIDRC_ALBUM_MODE, kDrcAlbumMode);
            aacDecoder_SetParam(decoder_, AAC_PCM_MAX_OUTPUT_CHANNELS, kMaxChannelCount);
            drc_.update();
            const UINT used = in_length[0] - bytes_valid[0];
            size -= used;
            offset += used;

            AAC_DECODER_ERROR err;
            do {
                if (space_left() < info_->frameSize * info_->numChannels) {
                    size = 0;  // not enough space left in the ring buffer: discard
                    break;
                }
                int consumed = static_cast<int>(info_->numTotalBytes);
                err = aacDecoder_DecodeFrame(decoder_, tmp, 2048 * kMaxChannelCount, 0);
                consumed = static_cast<int>(info_->numTotalBytes) - consumed;
                if (err == AAC_DEC_NOT_ENOUGH_BITS) break;
                in_info.decoded_sizes.push_back(consumed);
                if (bytes_valid[0] != 0) throw UnsupportedContainer("AAC: bytesValid != 0");
                const int samples = info_->frameSize * info_->numChannels;
                if (err != AAC_DEC_OK) {
                    std::memset(tmp, 0, static_cast<size_t>(samples) * sizeof(INT_PCM));  // silence
                    put(tmp, samples);
                    size = 0;  // discard the rest of the input
                    aacDecoder_SetParam(decoder_, AAC_TPDEC_CLEAR_BUFFER, 1);
                } else {
                    put(tmp, samples);
                }
                if ((info_->sampleRate && info_->numChannels &&
                     (info_->sampleRate != prev_rate || info_->numChannels != prev_channels)) ||
                    info_->outputLoudness != prev_loudness) {
                    in_info.format_update = true;
                    in_info.rate = info_->sampleRate;
                    in_info.channels = info_->numChannels;
                }
            } while (err == AAC_DEC_OK);
        }
        const int32_t output_delay = static_cast<int32_t>(info_->outputDelay) * info_->numChannels;
        infos_.push_back(std::move(in_info));
        if (!eos && delay_compensated_ < output_delay) {
            // discard outputDelay at the beginning
            const int32_t to_compensate = output_delay - delay_compensated_;
            const int32_t discard = std::min(filled_, to_compensate);
            delay_compensated_ += get(nullptr, discard);
            return;
        }
        if (eos) {
            drain_decoder();
            drain_ring_buffer(sink, true);
            for (const Info& info : infos_) {  // finished with no output
                if (info.format_update) sink.format_changed(info.rate, info.channels);
            }
            infos_.clear();
        } else {
            drain_ring_buffer(sink, false);
        }
    }

private:
    // A work not yet finished. An output format update (sample rate, channels, loudness)
    // travels with the work that caused it: the app sees it when that work's buffer comes
    // out, which with the decoder's delay is a few works later.
    struct Info {
        std::vector<int> decoded_sizes;
        bool format_update = false;
        int rate = 0, channels = 0;
    };
    HANDLE_AACDECODER decoder_ = nullptr;
    CStreamInfo* info_ = nullptr;
    CDrcPresModeWrapper drc_;
    std::vector<INT_PCM> ring_;
    int32_t write_pos_ = 0, read_pos_ = 0, filled_ = 0, delay_compensated_ = 0;
    std::deque<Info> infos_;
    std::vector<INT_PCM> block_;

    int32_t ring_size() const { return static_cast<int32_t>(ring_.size()); }
    int32_t space_left() const { return ring_size() - filled_; }

    void put(const INT_PCM* samples, int32_t n) {
        if (n <= 0) return;
        if (space_left() < n) throw UnsupportedContainer("AAC: ring buffer would overflow");
        for (int32_t i = 0; i < n; ++i) {
            ring_[static_cast<size_t>(write_pos_)] = samples[i];
            if (++write_pos_ >= ring_size()) write_pos_ -= ring_size();
        }
        filled_ += n;
    }
    int32_t get(INT_PCM* samples, int32_t n) {
        if (n > filled_) throw UnsupportedContainer("AAC: ring buffer would underrun");
        for (int32_t i = 0; i < n; ++i) {
            if (samples) samples[i] = ring_[static_cast<size_t>(read_pos_)];
            if (++read_pos_ >= ring_size()) read_pos_ -= ring_size();
        }
        filled_ -= n;
        return n;
    }

    void drain_ring_buffer(PcmSink& sink, bool eos) {
        while (!infos_.empty() && filled_ >= info_->frameSize * info_->numChannels) {
            const Info& front = infos_.front();
            const int32_t available = filled_;
            int32_t num_samples = static_cast<int32_t>(front.decoded_sizes.size()) * info_->frameSize * info_->numChannels;
            if (available < num_samples) {
                if (!eos) break;
                num_samples = available;
            }
            if (front.format_update) sink.format_changed(front.rate, front.channels);
            if (num_samples > 0) {
                block_.resize(static_cast<size_t>(num_samples));
                get(block_.data(), num_samples);
                sink.buffer(block_.data(), block_.size());
            }
            infos_.pop_front();
        }
    }

    // flush the decoder until the output delay is made up
    void drain_decoder() {
        INT_PCM tmp[2048 * kMaxChannelCount];
        while (delay_compensated_ > 0) {
            drc_.submitStreamData(info_);
            drc_.update();
            aacDecoder_DecodeFrame(decoder_, tmp, 2048 * kMaxChannelCount, AACDEC_FLUSH);
            int32_t n = info_->frameSize * info_->numChannels;
            if (n <= 0) throw UnsupportedContainer("AAC: nothing to flush");
            if (n > delay_compensated_) n = delay_compensated_;
            put(tmp, n);
            delay_compensated_ -= n;
        }
    }
};

}  // namespace

std::vector<int16_t> decode_mp3(Demuxer& demuxer, const std::function<void(double)>& progress) {
    const AudioTrack& track = demuxer.track();
    PcmSink sink(track);
    Mp3Decoder decoder;
    Progress report(demuxer, progress);
    Packet packet;
    while (demuxer.next(packet)) {
        decoder.process(packet.data.data(), packet.data.size(), false, sink);
        report.update();
    }
    decoder.process(nullptr, 0, true, sink);  // the end-of-stream buffer
    auto pcm = sink.finish();
    report.done();
    return pcm;
}

std::vector<int16_t> decode_aac(Demuxer& demuxer, const std::function<void(double)>& progress) {
    const AudioTrack& track = demuxer.track();
    if (track.csd.empty()) throw UnsupportedContainer("AAC without csd-0");
    PcmSink sink(track);
    AacDecoder decoder(track.csd[0]);
    Progress report(demuxer, progress);
    Packet packet;
    while (demuxer.next(packet)) {
        decoder.process(packet.data.data(), packet.data.size(), false, sink);
        report.update();
    }
    decoder.process(nullptr, 0, true, sink);
    auto pcm = sink.finish();
    report.done();
    return pcm;
}

}  // namespace chordchart
