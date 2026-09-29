#include "resample.hpp"

#include <algorithm>
#include <stdexcept>
#include <string>

#include "soxr.h"

namespace chordchart {

std::vector<float> resample_int16(const int16_t* pcm, size_t samples, double in_rate, double out_rate) {
    const size_t olen = static_cast<size_t>(static_cast<double>(samples) * out_rate / in_rate + 1);
    const soxr_io_spec_t io_spec = soxr_io_spec(SOXR_FLOAT32_S, SOXR_FLOAT32_S);
    const soxr_quality_spec_t quality_spec = soxr_quality_spec(SOXR_HQ, 0);
    soxr_error_t err = nullptr;
    soxr_t soxr = soxr_create(in_rate, out_rate, 1, &err, &io_spec, &quality_spec, nullptr);
    if (err) throw std::runtime_error(std::string("soxr: ") + err);

    std::vector<float> out(olen, 0.0f);
    const size_t div_len = static_cast<size_t>(std::max(1000.0, 48000 * in_rate / out_rate));
    std::vector<float> chunk(div_len);
    size_t out_pos = 0, odone = 0;
    for (size_t idx = 0; idx < samples && !err; idx += div_len) {
        const size_t len = std::min(div_len, samples - idx);
        for (size_t i = 0; i < len; ++i) chunk[i] = static_cast<float>(pcm[idx + i]) / 32768.0f;
        const float* in_ptrs[1] = {chunk.data()};
        float* out_ptrs[1] = {out.data() + out_pos};
        err = soxr_process(soxr, in_ptrs, len, nullptr, out_ptrs, olen - out_pos, &odone);
        out_pos += odone;
    }
    if (!err) {
        float* out_ptrs[1] = {out.data() + out_pos};
        err = soxr_process(soxr, nullptr, 0, nullptr, out_ptrs, olen - out_pos, &odone);
        out_pos += odone;
    }
    soxr_delete(soxr);
    if (err) throw std::runtime_error(std::string("soxr: ") + err);
    out.resize(out_pos);
    return out;
}

}  // namespace chordchart
