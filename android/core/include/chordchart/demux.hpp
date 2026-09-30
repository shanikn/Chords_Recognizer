// Reading compressed audio packets out of a file, in-process (no decoding).
//
// Android's MediaExtractor reads every packet through a separate process: for WebM/Opus
// (YouTube audio: a packet per 20 ms) that alone took ~3 ms per packet on the emulator, 19 s
// for a 2-minute song. This reads the container in the app instead; the packets still go
// to the platform's decoder, so the decoded audio is the same.
//
// Supported: WebM/Matroska and Ogg, with Opus or Vorbis audio. Anything else throws
// UnsupportedContainer, and the platform falls back to its own extractor.

#pragma once

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace chordchart {

struct UnsupportedContainer : std::runtime_error {
    using std::runtime_error::runtime_error;
};

struct AudioTrack {
    std::string mime;               // "audio/opus", "audio/vorbis"
    int sample_rate = 0;
    int channels = 0;
    int64_t duration_us = -1;       // -1: unknown
    // Codec-specific data as Android's MediaExtractor gives it (MediaFormat csd-0, -1, -2):
    // Opus: OpusHead, codec delay (ns, int64 LE), seek pre-roll (ns, int64 LE)
    // Vorbis: identification header, setup header
    std::vector<std::vector<uint8_t>> csd;
};

struct Packet {
    std::vector<uint8_t> data;
    int64_t time_us = 0;
};

class Demuxer {
public:
    virtual ~Demuxer() = default;
    const AudioTrack& track() const { return track_; }
    // The next packet of the audio track; false at the end.
    virtual bool next(Packet& packet) = 0;
    // Bytes read so far and in total, for progress.
    virtual uint64_t position() const = 0;
    virtual uint64_t size() const = 0;

    // Opens `path` (UTF-8); throws UnsupportedContainer if it isn't a supported format.
    static std::unique_ptr<Demuxer> open(const std::string& path);
    // The same, from the whole file's bytes (e.g. read by the app from a file descriptor).
    static std::unique_ptr<Demuxer> open(std::vector<uint8_t> data);

protected:
    AudioTrack track_;
};

}  // namespace chordchart
