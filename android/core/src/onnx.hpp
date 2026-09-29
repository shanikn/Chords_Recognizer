// A thin wrapper over ONNX Runtime's C++ API: one session per model, float32 in and out.

#pragma once

#include <memory>
#include <string>
#include <vector>

namespace chordchart {

struct Tensor {
    std::vector<int64_t> shape;
    std::vector<float> data;
};

struct OnnxOptions {
    int threads = 1;
    bool xnnpack = false;  // XNNPACK execution provider (ARM/x86 NEON/SSE kernels)
    bool arena = false;    // onnxruntime's CPU memory arena
};

class OnnxModel {
public:
    OnnxModel(const std::string& path, const OnnxOptions& options);
    ~OnnxModel();
    OnnxModel(const OnnxModel&) = delete;
    OnnxModel& operator=(const OnnxModel&) = delete;

    // One float32 input; returns every output, in the model's order. Thread-safe.
    std::vector<Tensor> run(const float* data, const std::vector<int64_t>& shape) const;

    bool uses_xnnpack() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace chordchart
