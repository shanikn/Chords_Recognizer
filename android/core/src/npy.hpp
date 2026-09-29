// Reading NumPy .npy files (the tables in the assets folder, and the golden files in
// tests): little-endian, C order, the few dtypes the analysis uses.

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace chordchart {

template <typename T>
struct Array {
    std::vector<size_t> shape;
    std::vector<T> data;

    size_t rows() const { return shape.empty() ? 0 : shape[0]; }
    size_t cols() const { return shape.size() < 2 ? 1 : shape[1]; }
    const T& at(size_t r, size_t c) const { return data[r * cols() + c]; }
    T& at(size_t r, size_t c) { return data[r * cols() + c]; }
};

// Loads `path`; its dtype must be T's (float <f4, double <f8, uint32 <u4, int16 <i2,
// uint8 |u1); throws std::runtime_error otherwise.
template <typename T>
Array<T> load_npy(const std::string& path);

}  // namespace chordchart
