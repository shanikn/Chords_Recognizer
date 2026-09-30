// MP3 files and MP4/M4A files with AAC audio, read in-process.
//
// Each reader hands the decoder exactly the packets Android's own extractor would (the MP3
// extractor and the MPEG-4 extractor, AOSP android-15.0.0_r1, media/module/extractors), so
// that decoding them in-process gives the same audio as MediaExtractor + MediaCodec:
//
// - MP3 (MP3Extractor.cpp): skip ID3v2 tags at the start; the first frame is one followed by
//   three more with the same fixed header bits; a Xing/Info or VBRI frame is metadata and is
//   skipped; after that, frame by frame, resyncing (again: a frame and three successors)
//   when the next bytes aren't a frame; the stream ends at the first frame that's cut short
//   or where no resync is found (an ID3v1 tag, for instance).
// - MP4 (MPEG4Extractor.cpp, SampleTable.cpp): the first audio track; its samples in order,
//   as the sample table (stsz/stz2, stsc, stco/co64) lays them out; csd-0 is the esds
//   DecoderSpecificInfo. Edit lists only shift timestamps there, so they don't matter here.

#include "demux_internal.hpp"

#include "decode_internal.hpp"

#include <algorithm>
#include <climits>
#include <cstdio>
#include <cstring>
#include <string>
#include <string_view>

namespace chordchart {

// GetMPEGAudioFrameSize (media/module/foundation/avc_utils.cpp)
bool mpeg_audio_frame_size(uint32_t header, size_t* frame_size, int* out_sampling_rate, int* out_channels,
                           int* out_bitrate, int* out_num_samples) {
    *frame_size = 0;
    if (out_sampling_rate) *out_sampling_rate = 0;
    if (out_channels) *out_channels = 0;
    if (out_bitrate) *out_bitrate = 0;
    if (out_num_samples) *out_num_samples = 1152;
    if ((header & 0xffe00000) != 0xffe00000) return false;
    const unsigned version = (header >> 19) & 3;
    if (version == 0x01) return false;
    const unsigned layer = (header >> 17) & 3;
    if (layer == 0x00) return false;
    const unsigned bitrate_index = (header >> 12) & 0x0f;
    if (bitrate_index == 0 || bitrate_index == 0x0f) return false;
    const unsigned sampling_rate_index = (header >> 10) & 3;
    if (sampling_rate_index == 3) return false;
    static const int kSamplingRateV1[] = {44100, 48000, 32000};
    int sampling_rate = kSamplingRateV1[sampling_rate_index];
    if (version == 2) {
        sampling_rate /= 2;
    } else if (version == 0) {
        sampling_rate /= 4;
    }
    const unsigned padding = (header >> 9) & 1;
    if (layer == 3) {  // layer I
        static const int kBitrateV1[] = {32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448};
        static const int kBitrateV2[] = {32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256};
        const int bitrate = version == 3 ? kBitrateV1[bitrate_index - 1] : kBitrateV2[bitrate_index - 1];
        if (out_bitrate) *out_bitrate = bitrate;
        *frame_size = static_cast<size_t>((12000 * bitrate / sampling_rate + static_cast<int>(padding)) * 4);
        if (out_num_samples) *out_num_samples = 384;
    } else {  // layer II or III
        static const int kBitrateV1L2[] = {32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384};
        static const int kBitrateV1L3[] = {32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320};
        static const int kBitrateV2[] = {8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160};
        int bitrate;
        if (version == 3) {
            bitrate = layer == 2 ? kBitrateV1L2[bitrate_index - 1] : kBitrateV1L3[bitrate_index - 1];
            if (out_num_samples) *out_num_samples = 1152;
        } else {
            bitrate = kBitrateV2[bitrate_index - 1];
            if (out_num_samples) *out_num_samples = layer == 1 ? 576 : 1152;
        }
        if (out_bitrate) *out_bitrate = bitrate;
        if (version == 3) {
            *frame_size = static_cast<size_t>(144000 * bitrate / sampling_rate) + padding;
        } else {
            const size_t tmp = layer == 1 ? 72000 : 144000;
            *frame_size = tmp * static_cast<size_t>(bitrate) / static_cast<size_t>(sampling_rate) + padding;
        }
    }
    if (out_sampling_rate) *out_sampling_rate = sampling_rate;
    if (out_channels) *out_channels = ((header >> 6) & 3) == 3 ? 1 : 2;
    return true;
}

namespace {

uint32_t u32(const uint8_t* p) { return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3]; }
uint16_t u16(const uint8_t* p) { return static_cast<uint16_t>(p[0] << 8 | p[1]); }
uint64_t u64(const uint8_t* p) { return uint64_t(u32(p)) << 32 | u32(p + 4); }

// __builtin_*_overflow, for compilers without them (MSVC, the PC tests)
bool mul_overflow(int64_t a, int64_t b, int64_t* r) {
#if defined(__GNUC__) || defined(__clang__)
    return __builtin_mul_overflow(a, b, r);
#else
    *r = static_cast<int64_t>(static_cast<uint64_t>(a) * static_cast<uint64_t>(b));
    return a != 0 && (*r / a != b || (a == -1 && b == INT64_MIN) || (b == -1 && a == INT64_MIN));
#endif
}
bool add_overflow(int64_t a, int64_t b, int64_t* r) {
    *r = static_cast<int64_t>(static_cast<uint64_t>(a) + static_cast<uint64_t>(b));
    return (a >= 0) == (b >= 0) && (*r >= 0) != (a >= 0);
}
bool sub_overflow(int64_t a, int64_t b, int64_t* r) {
    *r = static_cast<int64_t>(static_cast<uint64_t>(a) - static_cast<uint64_t>(b));
    return (a >= 0) != (b >= 0) && (*r >= 0) != (a >= 0);
}

// ---------------------------------------------------------------- MP3


// Everything must match except protection, bitrate, padding, private bits, mode, mode
// extension, copyright, original and emphasis.
constexpr uint32_t kMask = 0xfffe0c00;

class Mp3Demuxer : public Demuxer {
public:
    explicit Mp3Demuxer(std::vector<uint8_t> data) : d_(std::move(data)) {
        // MP3Extractor's Sniff, then its constructor
        int64_t pos = 0, post_id3_pos = 0;
        uint32_t header = 0;
        if (d_.size() < 5) throw UnsupportedContainer("not MP3");
        if (std::memcmp(d_.data(), "\x00\x00\x01\xba", 4) == 0 && (d_[4] >> 4) == 2)
            throw UnsupportedContainer("MPEG-1 program stream");
        if (!resync(0, &pos, &post_id3_pos, &header)) throw UnsupportedContainer("not MP3");
        first_frame_ = pos;
        fixed_header_ = header;
        const int64_t xing = xing_base(first_frame_);
        if (xing >= 0) {
            // XINGSeeker: LAME's encoder delay and padding, right after the Xing/Info header
            const int64_t lame = xing + 0xb1 - 0x24;
            if (readable(lame, 3)) {
                const uint8_t* b = &d_[static_cast<size_t>(lame)];
                track_.encoder_delay = (b[0] << 4) + (b[1] >> 4);
                track_.encoder_padding = ((b[1] & 0xf) << 8) + b[2];
            }
        }
        // iTunes' gapless info (an ID3 COMM "iTunSMPB") overrides that; reading it as
        // Android's ID3 parser does isn't done here, so such files go to the platform.
        if (post_id3_pos > 0 && id3_mentions_itunsmpb(static_cast<size_t>(post_id3_pos)))
            throw UnsupportedContainer("MP3 with iTunSMPB");
        if (xing >= 0 || has_vbri(post_id3_pos)) {
            // the Xing/Info or VBRI frame holds metadata; the audio starts after it
            size_t frame_size;
            mpeg_audio_frame_size(header, &frame_size);
            pos += static_cast<int64_t>(frame_size);
            if (!resync(0, &pos, &post_id3_pos, &header)) throw UnsupportedContainer("no MP3 frames after the Xing frame");
            first_frame_ = pos;
            fixed_header_ = header;
        }
        size_t frame_size;
        int sample_rate, channels, bitrate;
        mpeg_audio_frame_size(header, &frame_size, &sample_rate, &channels, &bitrate);
        const unsigned layer = 4 - ((header >> 17) & 3);
        if (layer != 3) throw UnsupportedContainer("MPEG audio layer " + std::to_string(layer));  // audio/mpeg-L1/L2
        track_.mime = "audio/mpeg";
        track_.sample_rate = sample_rate;
        track_.channels = channels;
        const int64_t data_length = static_cast<int64_t>(d_.size()) - first_frame_;
        track_.duration_us = bitrate > 0 ? 8000LL * data_length / bitrate : -1;
        cursor_ = first_frame_;
    }

    // MP3Source::read
    bool next(Packet& packet) override {
        size_t frame_size = 0;
        int bitrate, num_samples = 1152, sample_rate = 0;
        for (;;) {
            if (!readable(cursor_, 4)) return false;
            const uint32_t header = u32(&d_[static_cast<size_t>(cursor_)]);
            if ((header & kMask) == (fixed_header_ & kMask) &&
                mpeg_audio_frame_size(header, &frame_size, &sample_rate, nullptr, &bitrate, &num_samples))
                break;
            // lost sync
            int64_t pos = cursor_;
            if (!resync(fixed_header_, &pos, nullptr, nullptr)) return false;
            cursor_ = pos;
        }
        if (!readable(cursor_, frame_size)) return false;
        const auto* begin = &d_[static_cast<size_t>(cursor_)];
        packet.data.assign(begin, begin + frame_size);
        packet.time_us = samples_read_ * 1000000 / std::max(sample_rate, 1);
        cursor_ += static_cast<int64_t>(frame_size);
        samples_read_ += num_samples;
        return true;
    }
    uint64_t position() const override { return static_cast<uint64_t>(cursor_); }
    uint64_t size() const override { return d_.size(); }

private:
    std::vector<uint8_t> d_;
    int64_t first_frame_ = 0, cursor_ = 0, samples_read_ = 0;
    uint32_t fixed_header_ = 0;

    bool readable(int64_t pos, size_t n) const { return pos >= 0 && static_cast<uint64_t>(pos) + n <= d_.size(); }

    // Resync (MP3Extractor.cpp)
    bool resync(uint32_t match_header, int64_t* inout_pos, int64_t* post_id3_pos, uint32_t* out_header) const {
        if (post_id3_pos) *post_id3_pos = 0;
        if (*inout_pos == 0) {
            // skip ID3v2 tags at the very beginning
            for (;;) {
                if (!readable(*inout_pos, 10)) return false;
                const uint8_t* id3 = &d_[static_cast<size_t>(*inout_pos)];
                if (std::memcmp("ID3", id3, 3) != 0) break;
                const size_t len = (static_cast<size_t>(id3[6] & 0x7f) << 21) | (static_cast<size_t>(id3[7] & 0x7f) << 14) |
                                   (static_cast<size_t>(id3[8] & 0x7f) << 7) | (id3[9] & 0x7f);
                *inout_pos += static_cast<int64_t>(len + 10);
            }
            if (post_id3_pos) *post_id3_pos = *inout_pos;
        }
        const int64_t kMaxBytesChecked = 128 * 1024;
        for (int64_t pos = *inout_pos; pos < *inout_pos + kMaxBytesChecked && readable(pos, 4); ++pos) {
            const uint32_t header = u32(&d_[static_cast<size_t>(pos)]);
            if (match_header != 0 && (header & kMask) != (match_header & kMask)) continue;
            size_t frame_size;
            int sample_rate, num_channels, bitrate;
            if (!mpeg_audio_frame_size(header, &frame_size, &sample_rate, &num_channels, &bitrate)) continue;
            // a frame; now its successors
            int64_t test_pos = pos + static_cast<int64_t>(frame_size);
            bool valid = true;
            for (int j = 0; j < 3; ++j) {
                if (!readable(test_pos, 4)) {
                    valid = false;
                    break;
                }
                const uint32_t test_header = u32(&d_[static_cast<size_t>(test_pos)]);
                size_t test_frame_size;
                if ((test_header & kMask) != (header & kMask) || !mpeg_audio_frame_size(test_header, &test_frame_size)) {
                    valid = false;
                    break;
                }
                test_pos += static_cast<int64_t>(test_frame_size);
            }
            if (valid) {
                *inout_pos = pos;
                if (out_header) *out_header = header;
                return true;
            }
        }
        return false;
    }

    bool id3_mentions_itunsmpb(size_t end) const {
        static const std::string kAscii = "iTunSMPB";
        std::string utf16le, utf16be;
        for (char c : kAscii) {
            utf16le += c;
            utf16le += '\0';
            utf16be += '\0';
            utf16be += c;
        }
        const auto* begin = reinterpret_cast<const char*>(d_.data());
        const std::string_view tag(begin, std::min(end, d_.size()));
        return tag.find(kAscii) != std::string_view::npos || tag.find(utf16le) != std::string_view::npos ||
               tag.find(utf16be) != std::string_view::npos;
    }

    // XINGSeeker::CreateFromSource != NULL: the offset of the "Xing"/"Info" id, else -1
    int64_t xing_base(int64_t first_frame_pos) const {
        if (!readable(first_frame_pos, 4)) return -1;
        const uint32_t header = u32(&d_[static_cast<size_t>(first_frame_pos)]);
        size_t frame_size;
        int sampling_rate, num_channels, samples_per_frame;
        if (!mpeg_audio_frame_size(header, &frame_size, &sampling_rate, &num_channels, nullptr, &samples_per_frame))
            return -1;
        int64_t offset = first_frame_pos + 4;
        const uint8_t version = (d_[static_cast<size_t>(first_frame_pos) + 1] >> 3) & 3;
        if (version & 1) {
            offset += num_channels != 1 ? 32 : 17;
        } else {
            offset += num_channels != 1 ? 17 : 9;
        }
        if (!readable(offset, 4)) return -1;
        const uint8_t* id = &d_[static_cast<size_t>(offset)];
        if (std::memcmp(id, "Xing", 4) != 0 && std::memcmp(id, "Info", 4) != 0) return -1;
        const int64_t base = offset;
        offset += 4;
        if (!readable(offset, 4)) return -1;
        const uint32_t flags = u32(&d_[static_cast<size_t>(offset)]);
        offset += 4;
        if (flags & 0x0001) {
            if (!readable(offset, 4)) return -1;
            offset += 4;
        }
        if (flags & 0x0002) {
            if (!readable(offset, 4)) return -1;
            offset += 4;
        }
        if (flags & 0x0004) {
            if (!readable(offset + 1, 99)) return -1;
        }
        return base;
    }

    // VBRISeeker::CreateFromSource != NULL
    bool has_vbri(int64_t post_id3_pos) const {
        if (!readable(post_id3_pos, 4)) return false;
        size_t frame_size;
        int sample_rate;
        if (!mpeg_audio_frame_size(u32(&d_[static_cast<size_t>(post_id3_pos)]), &frame_size, &sample_rate)) return false;
        const int64_t pos = post_id3_pos + 4 + 32;
        if (!readable(pos, 26)) return false;
        const uint8_t* vbri = &d_[static_cast<size_t>(pos)];
        if (std::memcmp(vbri, "VBRI", 4) != 0) return false;
        const size_t entries = u16(vbri + 18), entry_size = u16(vbri + 22);
        if (entry_size < 1 || entry_size > 4) return false;
        return readable(pos + 26, entries * entry_size);
    }
};

// ---------------------------------------------------------------- MP4

struct Box {
    uint32_t type = 0;
    size_t body = 0, end = 0;  // body start, box end
};

constexpr uint32_t fourcc(const char (&s)[5]) { return uint32_t(s[0]) << 24 | uint32_t(s[1]) << 16 | uint32_t(s[2]) << 8 | uint32_t(s[3]); }

class Mp4Demuxer : public Demuxer {
public:
    explicit Mp4Demuxer(std::vector<uint8_t> data) : d_(std::move(data)) {
        bool found_moov = false;
        for (size_t pos = 0; pos < d_.size();) {
            Box box = read_box(pos, d_.size());
            if (box.type == fourcc("moov")) {
                found_moov = true;
                parse_moov(box);
            } else if (box.type == fourcc("moof")) {
                throw UnsupportedContainer("fragmented MP4");
            }
            pos = box.end;
        }
        if (!found_moov) throw UnsupportedContainer("no moov box");
        if (sizes_.empty()) throw UnsupportedContainer("no AAC audio track");
    }

    bool next(Packet& packet) override {
        if (index_ >= sizes_.size()) return false;
        const uint64_t offset = offsets_[index_], size = sizes_[index_];
        if (offset + size > d_.size()) return false;  // SampleTable read error: the extractor stops
        packet.data.assign(d_.begin() + static_cast<std::ptrdiff_t>(offset), d_.begin() + static_cast<std::ptrdiff_t>(offset + size));
        packet.time_us = static_cast<int64_t>(index_);
        ++index_;
        return true;
    }
    uint64_t position() const override { return sizes_.empty() ? 0 : static_cast<uint64_t>(index_) * d_.size() / sizes_.size(); }
    uint64_t size() const override { return d_.size(); }

private:
    std::vector<uint8_t> d_;
    std::vector<uint64_t> offsets_;
    std::vector<uint32_t> sizes_;
    size_t index_ = 0;

    Box read_box(size_t pos, size_t limit) const {
        if (pos + 8 > limit) throw UnsupportedContainer("truncated MP4 box");
        Box box;
        uint64_t size = u32(&d_[pos]);
        box.type = u32(&d_[pos + 4]);
        size_t header = 8;
        if (size == 1) {
            if (pos + 16 > limit) throw UnsupportedContainer("truncated MP4 box");
            size = u64(&d_[pos + 8]);
            header = 16;
        } else if (size == 0) {
            size = limit - pos;
        }
        if (size < header || pos + size > limit) throw UnsupportedContainer("bad MP4 box size");
        box.body = pos + header;
        box.end = pos + static_cast<size_t>(size);
        return box;
    }

    template <typename F>
    void children(const Box& parent, size_t skip, F&& f) const {
        for (size_t pos = parent.body + skip; pos + 8 <= parent.end;) {
            Box box = read_box(pos, parent.end);
            f(box);
            pos = box.end;
        }
    }

    // What decides the audio track's encoder delay and padding (MPEG4Extractor): an iTunes
    // "iTunSMPB" tag sets them on the track parsed last before the tag; an edit list on the
    // track then overrides both (getTrackMetaData), computed from the movie and media
    // timescales, the media duration and the sample rate.
    int trak_count_ = 0, audio_trak_ = -1;
    uint32_t movie_timescale_ = 0;
    struct Smpb {
        int trak, delay, padding;
    };
    std::vector<Smpb> smpb_;
    struct Elst {
        bool needs_processing = false;
        uint64_t segment_duration = 0;
        int64_t media_time = 0;
    } elst_;
    uint32_t media_timescale_ = 0;
    int64_t duration_us_ = -1;  // -1: none

    void parse_moov(const Box& moov) {
        children(moov, 0, [&](const Box& b) {
            if (b.type == fourcc("mvex")) throw UnsupportedContainer("fragmented MP4");
            if (b.type == fourcc("cmov")) throw UnsupportedContainer("compressed moov");
            if (b.type == fourcc("mvhd") && b.body + 32 <= b.end)
                movie_timescale_ = u32(&d_[b.body + (d_[b.body] == 1 ? 20 : 12)]);
            if (b.type == fourcc("trak")) {
                const int index = trak_count_++;
                if (audio_trak_ < 0) parse_trak(b, index);
                find_itunsmpb(b, index);  // trak/udta/meta: mLastTrack is this track
            }
            if (b.type == fourcc("udta") || b.type == fourcc("meta")) find_itunsmpb(b, trak_count_ - 1);
        });
        if (audio_trak_ < 0) return;
        for (const Smpb& s : smpb_) {
            if (s.trak < 0) throw UnsupportedContainer("iTunSMPB before any track");
            if (s.trak == audio_trak_) {
                track_.encoder_delay = s.delay;
                track_.encoder_padding = s.padding;
            }
        }
        apply_edit_list();
    }

    // moov/udta/meta/ilst/---- (mean "com.apple.iTunes", name "iTunSMPB", data)
    void find_itunsmpb(const Box& box, int last_trak) {
        if (box.type == fourcc("----")) {
            std::string mean, name, data;
            children(box, 0, [&](const Box& b) {
                const auto text = [&](size_t skip) {
                    return b.body + skip <= b.end ? std::string(reinterpret_cast<const char*>(&d_[b.body + skip]), b.end - b.body - skip)
                                                  : std::string();
                };
                if (b.type == fourcc("mean")) mean = text(4);
                if (b.type == fourcc("name")) name = text(4);
                if (b.type == fourcc("data")) data = text(8);
            });
            // the extractor keeps them as C strings
            for (std::string* s : {&mean, &name, &data}) s->resize(std::strlen(s->c_str()));
            int delay, padding;
            if (mean == "com.apple.iTunes" && name == "iTunSMPB" &&
                std::sscanf(data.c_str(), " %*x %x %x %*x", reinterpret_cast<unsigned*>(&delay),
                            reinterpret_cast<unsigned*>(&padding)) == 2)
                smpb_.push_back({last_trak, delay, padding});
            return;
        }
        size_t skip = 0;
        if (box.type == fourcc("meta")) {
            // ISO: a full box (version, flags) before its children; QuickTime: none
            skip = box.body + 8 <= box.end && u32(&d_[box.body + 4]) == fourcc("hdlr") ? 0 : 4;
        } else if (box.type != fourcc("udta") && box.type != fourcc("ilst") && box.type != fourcc("trak")) {
            return;
        }
        children(box, skip, [&](const Box& b) {
            if (b.type == fourcc("udta") || b.type == fourcc("meta") || b.type == fourcc("ilst") || b.type == fourcc("----"))
                find_itunsmpb(b, last_trak);
        });
    }

    // MPEG4Extractor::getTrackMetaData: gapless info from a one- or two-entry edit list
    void apply_edit_list() {
        if (!elst_.needs_processing || movie_timescale_ == 0 || duration_us_ < 0 || media_timescale_ == 0) return;
        if (elst_.segment_duration > static_cast<uint64_t>(INT64_MAX)) return;
        const int64_t segment_duration = static_cast<int64_t>(elst_.segment_duration);
        const int64_t media_time = elst_.media_time;
        const int64_t samplerate = track_.sample_rate;
        const int64_t halfscale = media_timescale_ / 2;
        int64_t delay = 0;
        if (media_time > 0) {
            if (mul_overflow(media_time, samplerate, &delay) || add_overflow(delay, halfscale, &delay))
                return;
            delay /= media_timescale_;
            if (delay > INT32_MAX || delay < INT32_MIN) return;
        }
        track_.encoder_delay = static_cast<int>(delay);
        int64_t padding_samples = 0;
        if (segment_duration > 0) {
            const int64_t header_timescale = movie_timescale_;
            int64_t scaled_duration, segment_duration_e6, media_time_scaled, media_time_scaled_e6, segment_end, padding;
            if (mul_overflow(duration_us_, header_timescale, &scaled_duration)) return;
            if (mul_overflow(segment_duration, 1000000, &segment_duration_e6) ||
                mul_overflow(media_time, header_timescale, &media_time_scaled) ||
                mul_overflow(media_time_scaled, 1000000, &media_time_scaled_e6))
                return;
            media_time_scaled_e6 /= media_timescale_;
            if (add_overflow(segment_duration_e6, media_time_scaled_e6, &segment_end) ||
                sub_overflow(scaled_duration, segment_end, &padding))
                return;
            if (padding > 0) {
                const int64_t halfscale_mht = header_timescale / 2;
                int64_t halfscale_e6, timescale_e6;
                if (mul_overflow(padding, samplerate, &padding_samples) ||
                    mul_overflow(halfscale_mht, 1000000, &halfscale_e6) ||
                    mul_overflow(header_timescale, 1000000, &timescale_e6) ||
                    add_overflow(padding_samples, halfscale_e6, &padding_samples))
                    return;
                padding_samples /= timescale_e6;
                if (padding_samples > INT32_MAX) return;
            }
        }
        track_.encoder_padding = static_cast<int>(padding_samples);
    }

    void parse_trak(const Box& trak, int index) {
        Box mdia{}, minf{}, stbl{}, edts{}, mdhd{};
        bool sound = false;
        children(trak, 0, [&](const Box& b) {
            if (b.type == fourcc("mdia")) mdia = b;
            if (b.type == fourcc("edts")) edts = b;
        });
        if (!mdia.type) return;
        children(mdia, 0, [&](const Box& b) {
            if (b.type == fourcc("hdlr") && b.body + 12 <= b.end) sound = u32(&d_[b.body + 8]) == fourcc("soun");
            if (b.type == fourcc("minf")) minf = b;
            if (b.type == fourcc("mdhd")) mdhd = b;
        });
        if (!sound || !minf.type) return;
        audio_trak_ = index;
        if (mdhd.type) parse_mdhd(mdhd);
        if (edts.type) {
            children(edts, 0, [&](const Box& b) {
                if (b.type == fourcc("elst")) parse_elst(b);
            });
        }
        children(minf, 0, [&](const Box& b) {
            if (b.type == fourcc("stbl")) stbl = b;
        });
        // The first audio track is the one the app's MediaExtractor loop picks. If it isn't
        // AAC, the platform decodes the file (fallback).
        if (!stbl.type) throw UnsupportedContainer("audio track without a sample table");
        Box stsd{}, stsz{}, stz2{}, stsc{}, stco{}, co64{};
        children(stbl, 0, [&](const Box& b) {
            if (b.type == fourcc("stsd")) stsd = b;
            if (b.type == fourcc("stsz")) stsz = b;
            if (b.type == fourcc("stz2")) stz2 = b;
            if (b.type == fourcc("stsc")) stsc = b;
            if (b.type == fourcc("stco")) stco = b;
            if (b.type == fourcc("co64")) co64 = b;
        });
        if (!stsd.type || !stsc.type || (!stsz.type && !stz2.type) || (!stco.type && !co64.type))
            throw UnsupportedContainer("incomplete MP4 sample table");
        parse_stsd(stsd);
        build_sample_table(stsz.type ? stsz : stz2, stz2.type && !stsz.type, stsc, stco.type ? stco : co64, !stco.type);
    }

    void parse_mdhd(const Box& mdhd) {
        if (mdhd.body + 1 > mdhd.end) return;
        const bool v1 = d_[mdhd.body] == 1;
        const size_t timescale_offset = mdhd.body + (v1 ? 20 : 12);
        if (timescale_offset + (v1 ? 12 : 8) > mdhd.end) throw UnsupportedContainer("short mdhd");
        media_timescale_ = u32(&d_[timescale_offset]);
        int64_t duration = 0;
        if (v1) {
            const uint64_t d = u64(&d_[timescale_offset + 4]);
            if (d != ~0ULL) duration = static_cast<int64_t>(d);
        } else {
            const uint32_t d = u32(&d_[timescale_offset + 4]);
            if (d != 0xffffffff) duration = d;
        }
        if (duration != 0 && media_timescale_ != 0) {
            const long double us = static_cast<long double>(duration) * 1000000 / media_timescale_;
            if (us < 0 || us > static_cast<long double>(INT64_MAX)) throw UnsupportedContainer("bad mdhd duration");
            duration_us_ = static_cast<int64_t>(us);
        }
    }

    void parse_elst(const Box& elst) {
        if (elst.body + 8 > elst.end) throw UnsupportedContainer("short elst");
        const uint8_t version = d_[elst.body];
        const uint32_t entries = u32(&d_[elst.body + 4]);
        if (entries > 2) return;  // ignored by the extractor
        size_t pos = elst.body + 8;
        bool empty_edit = false;
        for (uint32_t i = 0; i < entries; ++i) {
            uint64_t segment_duration;
            int64_t media_time;
            if (version == 0) {
                if (pos + 8 > elst.end) throw UnsupportedContainer("short elst");
                segment_duration = u32(&d_[pos]);
                media_time = static_cast<int32_t>(u32(&d_[pos + 4]));
                pos += 12;
            } else if (version == 1) {
                if (pos + 16 > elst.end) throw UnsupportedContainer("short elst");
                segment_duration = u64(&d_[pos]);
                media_time = static_cast<int64_t>(u64(&d_[pos + 8]));
                pos += 20;
            } else {
                throw UnsupportedContainer("bad elst version");
            }
            if (media_time == -1 && i == 0) {
                empty_edit = true;
            } else if (media_time >= 0 && i == 0) {
                elst_.media_time = media_time;
                elst_.segment_duration = segment_duration;
            }
            (void)empty_edit;  // the second entry only shifts timestamps
        }
        elst_.needs_processing = true;
    }

    // updateAudioTrackInfoFromESDS_MPEG4Audio: the sample rate and channel count the
    // extractor reports come from the AudioSpecificConfig (the AAC core's rate)
    void apply_audio_specific_config(const std::vector<uint8_t>& asc) {
        if (asc.size() < 2) throw UnsupportedContainer("short AudioSpecificConfig");
        size_t bit = 0;
        const auto bits = [&](int n) {
            uint32_t v = 0;
            for (int i = 0; i < n; ++i, ++bit) {
                if (bit / 8 >= asc.size()) throw UnsupportedContainer("short AudioSpecificConfig");
                v = (v << 1) | ((asc[bit / 8] >> (7 - bit % 8)) & 1);
            }
            return v;
        };
        static const int kSamplingRate[] = {96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350};
        uint32_t object_type = bits(5);
        if (object_type == 31) object_type = 32 + bits(6);
        const uint32_t freq_index = bits(4);
        int sample_rate;
        int channels;
        if (freq_index == 15) {
            sample_rate = static_cast<int>(bits(24));
            channels = static_cast<int>(bits(4));
        } else {
            channels = static_cast<int>(bits(4));
            if (freq_index == 13 || freq_index == 14) throw UnsupportedContainer("bad AAC sampling frequency index");
            sample_rate = kSamplingRate[freq_index];
        }
        switch (channels) {
            case 1: case 2: case 3: case 4: case 5: case 6: break;
            case 11: channels = 7; break;
            case 7: case 12: case 14: channels = 8; break;
            default: throw UnsupportedContainer("AAC channel configuration " + std::to_string(channels));  // 0: a PCE
        }
        track_.sample_rate = sample_rate;
        track_.channels = channels;
    }

    void parse_stsd(const Box& stsd) {
        if (stsd.body + 8 > stsd.end || u32(&d_[stsd.body + 4]) < 1) throw UnsupportedContainer("empty stsd");
        Box entry = read_box(stsd.body + 8, stsd.end);  // the first sample description
        if (entry.type != fourcc("mp4a")) throw UnsupportedContainer("MP4 audio isn't mp4a");
        // SampleEntry (8) + AudioSampleEntry (20); QuickTime versions 1 and 2 add 16 / 36 bytes
        if (entry.body + 28 > entry.end) throw UnsupportedContainer("short mp4a");
        const uint16_t version = u16(&d_[entry.body + 8]);
        track_.channels = u16(&d_[entry.body + 16]);
        track_.sample_rate = static_cast<int>(u32(&d_[entry.body + 24]) >> 16);
        const size_t skip = 28 + (version == 1 ? 16 : version == 2 ? 36 : 0);
        std::vector<uint8_t> dsi;
        bool found = false;
        auto find_esds = [&](const Box& parent, size_t offset, auto&& self) -> void {
            children(parent, offset, [&](const Box& b) {
                if (b.type == fourcc("esds") && !found) {
                    found = true;
                    parse_esds(b, dsi);
                } else if (b.type == fourcc("wave")) {
                    self(b, 0, self);
                }
            });
        };
        find_esds(entry, skip, find_esds);
        if (!found || dsi.empty()) throw UnsupportedContainer("no AAC decoder config");
        track_.mime = "audio/mp4a-latm";
        apply_audio_specific_config(dsi);
        track_.csd = {dsi};
    }

    // ES_Descriptor -> DecoderConfigDescriptor (MPEG-4 or MPEG-2 AAC) -> DecoderSpecificInfo
    void parse_esds(const Box& esds, std::vector<uint8_t>& dsi) const {
        size_t pos = esds.body + 4;  // version, flags
        auto descriptor = [&](size_t& p, size_t end, uint8_t& tag) -> size_t {
            if (p >= end) throw UnsupportedContainer("bad esds");
            tag = d_[p++];
            size_t len = 0;
            for (int i = 0; i < 4; ++i) {
                if (p >= end) throw UnsupportedContainer("bad esds");
                const uint8_t b = d_[p++];
                len = (len << 7) | (b & 0x7f);
                if (!(b & 0x80)) break;
            }
            if (p + len > end) throw UnsupportedContainer("bad esds");
            return len;
        };
        uint8_t tag;
        size_t len = descriptor(pos, esds.end, tag);
        if (tag != 0x03 || len < 3) throw UnsupportedContainer("no ES_Descriptor");
        const size_t es_end = pos + len;
        const uint8_t flags = d_[pos + 2];
        pos += 3;
        if (flags & 0x80) pos += 2;
        if (flags & 0x40) {
            if (pos >= es_end) throw UnsupportedContainer("bad esds");
            pos += 1 + d_[pos];
        }
        if (flags & 0x20) pos += 2;
        len = descriptor(pos, es_end, tag);
        if (tag != 0x04 || len < 13) throw UnsupportedContainer("no DecoderConfigDescriptor");
        const uint8_t object_type = d_[pos];
        if (object_type != 0x40) throw UnsupportedContainer("MP4 audio object type isn't MPEG-4 audio");
        const size_t config_end = pos + len;
        pos += 13;
        if (pos >= config_end) return;
        len = descriptor(pos, config_end, tag);
        if (tag != 0x05) return;
        dsi.assign(d_.begin() + static_cast<std::ptrdiff_t>(pos), d_.begin() + static_cast<std::ptrdiff_t>(pos + len));
    }

    void build_sample_table(const Box& sz, bool compact, const Box& sc, const Box& co, bool co64) {
        // sample sizes
        if (sz.body + 12 > sz.end) throw UnsupportedContainer("bad stsz");
        const uint32_t count = u32(&d_[sz.body + 8]);
        sizes_.resize(count);
        if (!compact) {
            const uint32_t fixed = u32(&d_[sz.body + 4]);
            if (fixed == 0 && sz.body + 12 + 4ULL * count > sz.end) throw UnsupportedContainer("bad stsz");
            for (uint32_t i = 0; i < count; ++i) sizes_[i] = fixed ? fixed : u32(&d_[sz.body + 12 + 4ULL * i]);
        } else {
            const uint8_t field = d_[sz.body + 7];
            if (field != 4 && field != 8 && field != 16) throw UnsupportedContainer("bad stz2");
            if (sz.body + 12 + (static_cast<uint64_t>(count) * field + 7) / 8 > sz.end) throw UnsupportedContainer("bad stz2");
            const uint8_t* p = &d_[sz.body + 12];
            for (uint32_t i = 0; i < count; ++i) {
                if (field == 16) sizes_[i] = u16(p + 2 * i);
                else if (field == 8) sizes_[i] = p[i];
                else sizes_[i] = (i & 1) ? p[i / 2] & 0x0f : p[i / 2] >> 4;
            }
        }
        // chunk offsets
        if (co.body + 8 > co.end) throw UnsupportedContainer("bad stco");
        const uint32_t chunks = u32(&d_[co.body + 4]);
        const size_t width = co64 ? 8 : 4;
        if (co.body + 8 + static_cast<uint64_t>(chunks) * width > co.end) throw UnsupportedContainer("bad stco");
        std::vector<uint64_t> chunk_offsets(chunks);
        for (uint32_t i = 0; i < chunks; ++i)
            chunk_offsets[i] = co64 ? u64(&d_[co.body + 8 + 8ULL * i]) : u32(&d_[co.body + 8 + 4ULL * i]);
        // sample-to-chunk
        if (sc.body + 8 > sc.end) throw UnsupportedContainer("bad stsc");
        const uint32_t entries = u32(&d_[sc.body + 4]);
        if (sc.body + 8 + 12ULL * entries > sc.end || entries == 0) throw UnsupportedContainer("bad stsc");
        offsets_.resize(count);
        uint32_t sample = 0;
        for (uint32_t e = 0; e < entries && sample < count; ++e) {
            const uint8_t* entry = &d_[sc.body + 8 + 12ULL * e];
            const uint32_t first = u32(entry), per_chunk = u32(entry + 4);
            const uint32_t last = e + 1 < entries ? u32(entry + 12) : chunks + 1;  // exclusive, 1-based
            if (first < 1 || last < first) throw UnsupportedContainer("bad stsc");
            for (uint32_t c = first; c < last && sample < count; ++c) {
                if (c > chunks) throw UnsupportedContainer("bad stsc");
                uint64_t offset = chunk_offsets[c - 1];
                for (uint32_t s = 0; s < per_chunk && sample < count; ++s) {
                    offsets_[sample] = offset;
                    offset += sizes_[sample];
                    ++sample;
                }
            }
        }
        if (sample < count) {  // the extractor's reads fail past what the table covers
            sizes_.resize(sample);
            offsets_.resize(sample);
        }
    }
};

}  // namespace

std::unique_ptr<Demuxer> open_mp4(std::vector<uint8_t>& data) {
    if (data.size() < 8) return nullptr;
    const uint32_t type = u32(&data[4]);
    if (type != fourcc("ftyp") && type != fourcc("moov") && type != fourcc("mdat") && type != fourcc("free") &&
        type != fourcc("wide") && type != fourcc("skip"))
        return nullptr;
    return std::make_unique<Mp4Demuxer>(std::move(data));
}

std::unique_ptr<Demuxer> open_mp3(std::vector<uint8_t>& data) {
    // Formats whose own extractor wins over MP3's low-confidence sniff: never scan them for
    // frame sync (PCM or FLAC data can look like MP3 frames).
    static const char* kOther[] = {"RIFF", "fLaC", "FORM", "#!AMR", "MThd", "OggS", "\x1A\x45\xDF\xA3"};
    for (const char* magic : kOther) {
        const size_t n = std::strlen(magic);
        if (data.size() >= n && std::memcmp(data.data(), magic, n) == 0) throw UnsupportedContainer("not MP3");
    }
    if (data.size() > 188 && data[0] == 0x47 && data[188] == 0x47) throw UnsupportedContainer("MPEG-TS");
    return std::make_unique<Mp3Demuxer>(std::move(data));
}

}  // namespace chordchart
