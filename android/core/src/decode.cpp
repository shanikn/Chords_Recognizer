#include "chordchart/decode.hpp"

#include <opus_multistream.h>

#include <cstring>
#include <memory>
#include <stdexcept>

#include "chordchart/audio.hpp"
#include "chordchart/demux.hpp"

namespace chordchart {

namespace {

constexpr int kRate = 48000;
constexpr int kMaxChannels = 8;
constexpr int kMaxOpusOutputPacketSizeSamples = 960 * 6;

struct OpusHeader {
    int channels = 0, num_streams = 0, num_coupled = 0, gain_db = 0;
    uint8_t stream_map[kMaxChannels] = {0};
};

// As AOSP's ParseOpusHeader (media/libstagefright/foundation/OpusHeader.cpp).
OpusHeader parse_header(const std::vector<uint8_t>& h) {
    if (h.size() < 19) throw std::runtime_error("OpusHead too short");
    OpusHeader header;
    header.channels = h[9];
    if (header.channels < 1 || header.channels > kMaxChannels) throw std::runtime_error("unsupported Opus channel count");
    header.gain_db = static_cast<int16_t>(h[16] | (h[17] << 8));
    const int family = h[18];
    if (family == 0) {
        if (header.channels > 2) throw std::runtime_error("bad Opus channel mapping");
        header.num_streams = 1;
        header.num_coupled = header.channels > 1;
        header.stream_map[0] = 0;
        header.stream_map[1] = 1;
    } else if (family == 1) {
        if (h.size() < 21u + header.channels) throw std::runtime_error("OpusHead too short");
        header.num_streams = h[19];
        header.num_coupled = h[20];
        std::memcpy(header.stream_map, &h[21], header.channels);
    } else {
        throw std::runtime_error("unsupported Opus channel mapping family");
    }
    return header;
}

uint64_t ns_to_samples(uint64_t ns, int rate) { return static_cast<uint64_t>(static_cast<double>(ns) * rate / 1000000000); }

int64_t read_int64_le(const std::vector<uint8_t>& b) {
    if (b.size() < 8) return 0;
    uint64_t v = 0;
    for (int i = 7; i >= 0; --i) v = (v << 8) | b[static_cast<size_t>(i)];
    return static_cast<int64_t>(v);
}

}  // namespace

std::vector<int16_t> decode_file(const std::string& path, const std::function<void(double)>& progress) {
    auto demuxer = Demuxer::open(path);
    const AudioTrack& track = demuxer->track();
    if (track.mime != "audio/opus" || track.csd.size() < 3) throw UnsupportedContainer("not Opus");

    const OpusHeader header = parse_header(track.csd[0]);
    uint8_t channel_mapping[kMaxChannels] = {0};
    if (header.channels <= 2) {
        channel_mapping[0] = 0;
        channel_mapping[1] = 1;
    } else {
        std::memcpy(channel_mapping, header.stream_map, header.channels);
    }
    int status = OPUS_INVALID_STATE;
    std::unique_ptr<OpusMSDecoder, void (*)(OpusMSDecoder*)> decoder(
        opus_multistream_decoder_create(kRate, header.channels, header.num_streams, header.num_coupled, channel_mapping,
                                        &status),
        opus_multistream_decoder_destroy);
    if (!decoder || status != OPUS_OK) throw std::runtime_error(std::string("Opus: ") + opus_strerror(status));
    opus_multistream_decoder_ctl(decoder.get(), OPUS_SET_GAIN(header.gain_db));

    const uint64_t codec_delay = ns_to_samples(static_cast<uint64_t>(read_int64_le(track.csd[1])), kRate);
    uint64_t to_discard = codec_delay;

    MonoResampler resampler(kRate, header.channels);
    std::vector<int16_t> out(static_cast<size_t>(kMaxOpusOutputPacketSizeSamples) * header.channels);
    Packet packet;
    double reported = -1;
    while (demuxer->next(packet)) {
        if (packet.data.empty()) continue;
        // C2SoftOpusDec: a packet at timestamp 0 restarts the codec-delay discard
        if (packet.time_us == 0) to_discard = codec_delay;
        int samples = opus_multistream_decode(decoder.get(), packet.data.data(), static_cast<opus_int32>(packet.data.size()),
                                              out.data(), kMaxOpusOutputPacketSizeSamples, 0);
        if (samples < 0) throw std::runtime_error(std::string("Opus: ") + opus_strerror(samples));
        size_t offset = 0;
        if (to_discard > 0) {
            if (to_discard > static_cast<uint64_t>(samples)) {
                to_discard -= static_cast<uint64_t>(samples);
                samples = 0;
            } else {
                samples -= static_cast<int>(to_discard);
                offset = static_cast<size_t>(to_discard) * header.channels;
                to_discard = 0;
            }
        }
        if (samples > 0) resampler.feed(out.data() + offset, static_cast<size_t>(samples) * header.channels);
        if (progress && demuxer->size()) {
            const double f = static_cast<double>(demuxer->position()) / static_cast<double>(demuxer->size());
            if (f - reported >= 0.01) {
                reported = f;
                progress(f);
            }
        }
    }
    auto pcm = resampler.finish();
    if (progress) progress(1.0);
    return pcm;
}

}  // namespace chordchart
