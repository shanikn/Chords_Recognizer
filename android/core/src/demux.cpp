#include "chordchart/demux.hpp"

#include <cstring>
#include <deque>
#include <fstream>
#include <iterator>

namespace chordchart {

namespace {

std::vector<uint8_t> read_file(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot open " + path);
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(in), {});
}

std::vector<uint8_t> int64_le(int64_t v) {
    std::vector<uint8_t> out(8);
    for (int i = 0; i < 8; ++i) out[i] = static_cast<uint8_t>((static_cast<uint64_t>(v) >> (8 * i)) & 0xFF);
    return out;
}

// Opus codec-specific data, as MediaExtractor gives it: OpusHead, codec delay, pre-roll.
void opus_csd(AudioTrack& track, const std::vector<uint8_t>& head, int64_t codec_delay_ns, int64_t seek_preroll_ns) {
    if (head.size() < 19 || std::memcmp(head.data(), "OpusHead", 8) != 0) throw UnsupportedContainer("bad OpusHead");
    track.mime = "audio/opus";
    track.channels = head[9];
    track.sample_rate = 48000;  // Opus always decodes at 48 kHz
    if (codec_delay_ns < 0) {
        const unsigned pre_skip = head[10] | (head[11] << 8);
        codec_delay_ns = static_cast<int64_t>(pre_skip) * 1000000000LL / 48000;
    }
    track.csd = {head, int64_le(codec_delay_ns), int64_le(seek_preroll_ns)};
}

// ---------------------------------------------------------------- Matroska / WebM

constexpr uint32_t kEbml = 0x1A45DFA3, kSegment = 0x18538067, kInfo = 0x1549A966, kTracks = 0x1654AE6B,
                   kCluster = 0x1F43B675, kTimecodeScale = 0x2AD7B1, kDuration = 0x4489, kTrackEntry = 0xAE,
                   kTrackNumber = 0xD7, kTrackType = 0x83, kCodecId = 0x86, kCodecPrivate = 0x63A2,
                   kCodecDelay = 0x56AA, kSeekPreRoll = 0x56BB, kAudio = 0xE1, kSamplingFrequency = 0xB5,
                   kChannels = 0x9F, kTimecode = 0xE7, kSimpleBlock = 0xA3, kBlockGroup = 0xA0, kBlock = 0xA1,
                   kDocType = 0x4282;
constexpr uint64_t kUnknownSize = ~0ULL;

class MatroskaDemuxer : public Demuxer {
public:
    explicit MatroskaDemuxer(std::vector<uint8_t> data) : d_(std::move(data)) {
        size_t pos = 0;
        uint32_t id = read_id(pos);
        uint64_t size = read_size(pos);
        if (id != kEbml) throw UnsupportedContainer("not EBML");
        std::string doctype;
        for (size_t p = pos, end = pos + size; p < end;) {
            uint32_t cid = read_id(p);
            uint64_t csize = read_size(p);
            if (cid == kDocType) doctype.assign(reinterpret_cast<const char*>(&d_[p]), csize);
            p += csize;
        }
        if (doctype != "webm" && doctype != "matroska") throw UnsupportedContainer("EBML doctype " + doctype);
        pos += size;
        if (read_id(pos) != kSegment) throw UnsupportedContainer("no Segment");
        uint64_t seg_size = read_size(pos);
        seg_end_ = seg_size == kUnknownSize ? d_.size() : std::min<uint64_t>(d_.size(), pos + seg_size);
        // metadata: everything before the first Cluster
        double duration = -1;
        for (size_t p = pos; p < seg_end_;) {
            const size_t start = p;
            uint32_t cid = read_id(p);
            uint64_t csize = read_size(p);
            if (cid == kCluster) {
                cursor_ = start;
                break;
            }
            if (csize == kUnknownSize) throw UnsupportedContainer("unknown-size element before clusters");
            if (cid == kInfo) parse_info(p, p + csize, duration);
            if (cid == kTracks) parse_tracks(p, p + csize);
            p += csize;
            cursor_ = p;
        }
        if (track_number_ == 0) throw UnsupportedContainer("no Opus audio track");
        if (duration >= 0) track_.duration_us = static_cast<int64_t>(duration * timecode_scale_ / 1000.0);
    }

    bool next(Packet& packet) override {
        while (pending_.empty()) {
            if (!advance()) return false;
        }
        packet = std::move(pending_.front());
        pending_.pop_front();
        return true;
    }
    uint64_t position() const override { return cursor_; }
    uint64_t size() const override { return d_.size(); }

private:
    std::vector<uint8_t> d_;
    size_t seg_end_ = 0, cursor_ = 0, cluster_end_ = 0;
    bool in_cluster_ = false;
    uint64_t timecode_scale_ = 1000000;
    int64_t cluster_timecode_ = 0;
    uint64_t track_number_ = 0;
    std::deque<Packet> pending_;

    void need(size_t pos, size_t n) const {
        if (pos + n > d_.size()) throw UnsupportedContainer("truncated file");
    }
    uint32_t read_id(size_t& pos) const {
        need(pos, 1);
        const uint8_t first = d_[pos];
        int len = first & 0x80 ? 1 : first & 0x40 ? 2 : first & 0x20 ? 3 : first & 0x10 ? 4 : 0;
        if (!len) throw UnsupportedContainer("bad element id");
        need(pos, len);
        uint32_t id = 0;
        for (int i = 0; i < len; ++i) id = (id << 8) | d_[pos + i];
        pos += len;
        return id;
    }
    uint64_t read_vint(size_t& pos, bool* unknown = nullptr) const {
        need(pos, 1);
        const uint8_t first = d_[pos];
        int len = 1;
        while (len <= 8 && !(first & (0x80 >> (len - 1)))) ++len;
        if (len > 8) throw UnsupportedContainer("bad vint");
        need(pos, len);
        uint64_t v = first & (0xFF >> len);
        bool all_ones = v == (0xFFULL >> len);
        for (int i = 1; i < len; ++i) {
            v = (v << 8) | d_[pos + i];
            all_ones = all_ones && d_[pos + i] == 0xFF;
        }
        pos += len;
        if (unknown) *unknown = all_ones;
        return v;
    }
    uint64_t read_size(size_t& pos) const {
        bool unknown = false;
        uint64_t v = read_vint(pos, &unknown);
        return unknown ? kUnknownSize : v;
    }
    uint64_t read_uint(size_t pos, uint64_t size) const {
        need(pos, size);
        uint64_t v = 0;
        for (uint64_t i = 0; i < size; ++i) v = (v << 8) | d_[pos + i];
        return v;
    }
    double read_float(size_t pos, uint64_t size) const {
        need(pos, size);
        if (size == 4) {
            uint32_t bits = static_cast<uint32_t>(read_uint(pos, 4));
            float f;
            std::memcpy(&f, &bits, 4);
            return f;
        }
        if (size == 8) {
            uint64_t bits = read_uint(pos, 8);
            double f;
            std::memcpy(&f, &bits, 8);
            return f;
        }
        return 0;
    }

    void parse_info(size_t p, size_t end, double& duration) {
        while (p < end) {
            uint32_t id = read_id(p);
            uint64_t size = read_size(p);
            if (id == kTimecodeScale) timecode_scale_ = read_uint(p, size);
            if (id == kDuration) duration = read_float(p, size);
            p += size;
        }
    }

    void parse_tracks(size_t p, size_t end) {
        while (p < end) {
            uint32_t id = read_id(p);
            uint64_t size = read_size(p);
            if (id == kTrackEntry && track_number_ == 0) parse_track(p, p + size);
            p += size;
        }
    }

    void parse_track(size_t p, size_t end) {
        uint64_t number = 0, type = 0;
        std::string codec;
        std::vector<uint8_t> priv;
        int64_t codec_delay = -1, preroll = 80000000;
        while (p < end) {
            uint32_t id = read_id(p);
            uint64_t size = read_size(p);
            if (id == kTrackNumber) number = read_uint(p, size);
            else if (id == kTrackType) type = read_uint(p, size);
            else if (id == kCodecId) codec.assign(reinterpret_cast<const char*>(&d_[p]), size);
            else if (id == kCodecPrivate) priv.assign(d_.begin() + static_cast<long>(p), d_.begin() + static_cast<long>(p + size));
            else if (id == kCodecDelay) codec_delay = static_cast<int64_t>(read_uint(p, size));
            else if (id == kSeekPreRoll) preroll = static_cast<int64_t>(read_uint(p, size));
            p += size;
        }
        if (type != 2 || codec != "A_OPUS") return;  // audio, Opus only (Vorbis: fallback)
        opus_csd(track_, priv, codec_delay, preroll);
        track_number_ = number;
    }

    // Reads elements until at least one packet is pending; false at the end.
    bool advance() {
        while (true) {
            if (in_cluster_ && cursor_ >= cluster_end_) in_cluster_ = false;
            if (cursor_ >= seg_end_) return false;
            size_t p = cursor_;
            uint32_t id = read_id(p);
            uint64_t size = read_size(p);
            if (id == kCluster) {
                in_cluster_ = true;
                cluster_end_ = size == kUnknownSize ? seg_end_ : std::min<size_t>(seg_end_, p + size);
                cursor_ = p;  // step into the cluster
                continue;
            }
            if (size == kUnknownSize) throw UnsupportedContainer("unknown-size element");
            const size_t body = p, next_pos = p + size;
            if (in_cluster_) {
                if (id == kTimecode) cluster_timecode_ = static_cast<int64_t>(read_uint(body, size));
                if (id == kSimpleBlock) parse_block(body, size);
                if (id == kBlockGroup) {
                    for (size_t q = body; q < next_pos;) {
                        uint32_t cid = read_id(q);
                        uint64_t csize = read_size(q);
                        if (cid == kBlock) parse_block(q, csize);
                        q += csize;
                    }
                }
            }
            cursor_ = next_pos;
            if (!pending_.empty()) return true;
        }
    }

    void parse_block(size_t p, uint64_t size) {
        const size_t end = p + size;
        uint64_t track = read_vint(p);
        if (track != track_number_) return;
        need(p, 3);
        const int16_t rel = static_cast<int16_t>((d_[p] << 8) | d_[p + 1]);
        const uint8_t flags = d_[p + 2];
        p += 3;
        const int64_t time_us =
            static_cast<int64_t>((cluster_timecode_ + rel) * static_cast<double>(timecode_scale_) / 1000.0);
        const int lacing = (flags >> 1) & 3;
        if (lacing == 0) {
            pending_.push_back({std::vector<uint8_t>(d_.begin() + static_cast<long>(p), d_.begin() + static_cast<long>(end)), time_us});
            return;
        }
        need(p, 1);
        const size_t frames = d_[p] + 1u;
        ++p;
        std::vector<size_t> sizes(frames, 0);
        if (lacing == 1) {  // Xiph
            for (size_t i = 0; i + 1 < frames; ++i) {
                size_t s = 0;
                uint8_t b;
                do {
                    need(p, 1);
                    b = d_[p++];
                    s += b;
                } while (b == 255);
                sizes[i] = s;
            }
        } else if (lacing == 3) {  // EBML
            size_t prev = read_vint(p);
            sizes[0] = prev;
            for (size_t i = 1; i + 1 < frames; ++i) {
                size_t q = p;
                uint64_t raw = read_vint(q);
                const int len = static_cast<int>(q - p);
                const int64_t bias = (1LL << (7 * len - 1)) - 1;
                p = q;
                prev = static_cast<size_t>(static_cast<int64_t>(prev) + static_cast<int64_t>(raw) - bias);
                sizes[i] = prev;
            }
        }
        size_t used = 0;
        for (size_t i = 0; i + 1 < frames; ++i) used += sizes[i];
        if (lacing == 2) {  // fixed
            const size_t each = (end - p) / frames;
            for (auto& s : sizes) s = each;
        } else {
            if (end < p + used) throw UnsupportedContainer("bad lacing");
            sizes[frames - 1] = end - p - used;
        }
        for (size_t s : sizes) {
            need(p, s);
            pending_.push_back({std::vector<uint8_t>(d_.begin() + static_cast<long>(p), d_.begin() + static_cast<long>(p + s)), time_us});
            p += s;
        }
    }
};

// ---------------------------------------------------------------- Ogg

class OggDemuxer : public Demuxer {
public:
    explicit OggDemuxer(std::vector<uint8_t> data) : d_(std::move(data)) {
        // the first logical stream: OpusHead in its first packet
        std::vector<uint8_t> head;
        if (!read_packet(head, true)) throw UnsupportedContainer("empty Ogg");
        if (head.size() < 8 || std::memcmp(head.data(), "OpusHead", 8) != 0)
            throw UnsupportedContainer("Ogg without Opus");  // Vorbis/FLAC: fallback
        opus_csd(track_, head, -1, 80000000);
        std::vector<uint8_t> tags;
        read_packet(tags, false);  // OpusTags
    }

    bool next(Packet& packet) override {
        packet.time_us = last_granule_ >= 0 ? last_granule_ * 1000000 / 48000 : 0;
        return read_packet(packet.data, false);
    }
    uint64_t position() const override { return pos_; }
    uint64_t size() const override { return d_.size(); }

private:
    std::vector<uint8_t> d_;
    size_t pos_ = 0;
    int64_t serial_ = -1, last_granule_ = -1;
    std::vector<size_t> segs_;  // remaining lacing values of the current page
    size_t seg_index_ = 0, seg_data_ = 0;

    bool next_page() {
        while (pos_ + 27 <= d_.size()) {
            if (std::memcmp(&d_[pos_], "OggS", 4) != 0) throw UnsupportedContainer("lost Ogg sync");
            const uint32_t serial = d_[pos_ + 14] | (d_[pos_ + 15] << 8) | (d_[pos_ + 16] << 16) | (static_cast<uint32_t>(d_[pos_ + 17]) << 24);
            int64_t granule = 0;
            for (int i = 7; i >= 0; --i) granule = (granule << 8) | d_[pos_ + 6 + i];
            const size_t nsegs = d_[pos_ + 26];
            if (pos_ + 27 + nsegs > d_.size()) return false;
            size_t body = 0;
            std::vector<size_t> segs(nsegs);
            for (size_t i = 0; i < nsegs; ++i) body += segs[i] = d_[pos_ + 27 + i];
            const size_t data_start = pos_ + 27 + nsegs;
            if (data_start + body > d_.size()) return false;
            const size_t page = pos_;
            pos_ = data_start + body;
            if (serial_ < 0) serial_ = serial;
            if (serial != serial_) continue;  // another logical stream
            (void)page;
            segs_ = std::move(segs);
            seg_index_ = 0;
            seg_data_ = data_start;
            if (granule != -1) last_granule_ = granule;
            return true;
        }
        return false;
    }

    // Packets span lacing values; a value < 255 ends one (possibly across pages).
    bool read_packet(std::vector<uint8_t>& out, bool first) {
        out.clear();
        bool any = false;
        while (true) {
            if (seg_index_ >= segs_.size()) {
                if (!next_page()) return any && !out.empty();
                if (first && serial_ < 0) return false;
            }
            while (seg_index_ < segs_.size()) {
                const size_t s = segs_[seg_index_++];
                out.insert(out.end(), d_.begin() + static_cast<long>(seg_data_), d_.begin() + static_cast<long>(seg_data_ + s));
                seg_data_ += s;
                any = true;
                if (s < 255) return true;
            }
        }
    }
};

}  // namespace

std::unique_ptr<Demuxer> Demuxer::open(const std::string& path) {
    std::vector<uint8_t> data = read_file(path);
    if (data.size() >= 4 && data[0] == 0x1A && data[1] == 0x45 && data[2] == 0xDF && data[3] == 0xA3)
        return std::make_unique<MatroskaDemuxer>(std::move(data));
    if (data.size() >= 4 && std::memcmp(data.data(), "OggS", 4) == 0) return std::make_unique<OggDemuxer>(std::move(data));
    throw UnsupportedContainer("not WebM, Matroska or Ogg");
}

}  // namespace chordchart
