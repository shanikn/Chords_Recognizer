#include "onnx.hpp"

#include <onnxruntime_cxx_api.h>

#include <algorithm>
#include <string>
#include <unordered_map>

namespace chordchart {

namespace {

Ort::Env& env() {
    static Ort::Env instance(ORT_LOGGING_LEVEL_WARNING, "chordchart");
    return instance;
}

#ifdef _WIN32
std::wstring model_path(const std::string& utf8) {
    // ONNX Runtime takes wide paths on Windows (the PC tests); UTF-8 elsewhere.
    std::wstring out;
    for (size_t i = 0; i < utf8.size();) {
        unsigned char c = static_cast<unsigned char>(utf8[i]);
        unsigned code = c;
        int extra = c >= 0xF0 ? 3 : c >= 0xE0 ? 2 : c >= 0xC0 ? 1 : 0;
        if (extra) code = c & (0x3F >> extra);
        for (int k = 1; k <= extra && i + k < utf8.size(); ++k) code = (code << 6) | (utf8[i + k] & 0x3F);
        i += 1 + extra;
        if (code >= 0x10000) {
            code -= 0x10000;
            out += static_cast<wchar_t>(0xD800 + (code >> 10));
            out += static_cast<wchar_t>(0xDC00 + (code & 0x3FF));
        } else {
            out += static_cast<wchar_t>(code);
        }
    }
    return out;
}
#else
const std::string& model_path(const std::string& utf8) { return utf8; }
#endif

}  // namespace

struct OnnxModel::Impl {
    Ort::SessionOptions options;
    std::unique_ptr<Ort::Session> session;
    std::vector<std::string> input_names, output_names;
    bool xnnpack = false;
};

OnnxModel::OnnxModel(const std::string& path, const OnnxOptions& opts) : impl_(std::make_unique<Impl>()) {
    auto& o = impl_->options;
    o.SetInterOpNumThreads(1);
    o.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
    if (!opts.arena) o.DisableCpuMemArena();
    if (opts.xnnpack) {
        // XNNPACK runs its own thread pool; ONNX Runtime's is then kept to one thread so
        // the two don't compete (ONNX Runtime's recommendation for this provider).
        std::unordered_map<std::string, std::string> provider{{"intra_op_num_threads", std::to_string(std::max(1, opts.threads))}};
        o.AppendExecutionProvider("XNNPACK", provider);
        o.SetIntraOpNumThreads(1);
        o.AddConfigEntry("session.intra_op.allow_spinning", "0");
        impl_->xnnpack = true;
    } else {
        o.SetIntraOpNumThreads(std::max(1, opts.threads));
    }
    impl_->session = std::make_unique<Ort::Session>(env(), model_path(path).c_str(), o);
    Ort::AllocatorWithDefaultOptions allocator;
    for (size_t i = 0; i < impl_->session->GetInputCount(); ++i)
        impl_->input_names.emplace_back(impl_->session->GetInputNameAllocated(i, allocator).get());
    for (size_t i = 0; i < impl_->session->GetOutputCount(); ++i)
        impl_->output_names.emplace_back(impl_->session->GetOutputNameAllocated(i, allocator).get());
}

OnnxModel::~OnnxModel() = default;

bool OnnxModel::uses_xnnpack() const { return impl_->xnnpack; }

std::vector<Tensor> OnnxModel::run(const float* data, const std::vector<int64_t>& shape) const {
    size_t count = 1;
    for (auto d : shape) count *= static_cast<size_t>(d);
    auto memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
    Ort::Value input = Ort::Value::CreateTensor<float>(memory, const_cast<float*>(data), count, shape.data(), shape.size());
    std::vector<const char*> in_names{impl_->input_names[0].c_str()};
    std::vector<const char*> out_names;
    for (const auto& n : impl_->output_names) out_names.push_back(n.c_str());
    auto outputs = impl_->session->Run(Ort::RunOptions{nullptr}, in_names.data(), &input, 1, out_names.data(), out_names.size());
    std::vector<Tensor> result;
    for (auto& value : outputs) {
        auto info = value.GetTensorTypeAndShapeInfo();
        Tensor t;
        t.shape = info.GetShape();
        const float* p = value.GetTensorData<float>();
        t.data.assign(p, p + info.GetElementCount());
        result.push_back(std::move(t));
    }
    return result;
}

}  // namespace chordchart
