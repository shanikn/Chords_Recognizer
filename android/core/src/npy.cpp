#include "npy.hpp"

#include <cstring>
#include <fstream>
#include <stdexcept>
#include <type_traits>

namespace chordchart {

namespace {

template <typename T>
const char* descr() {
    if constexpr (std::is_same_v<T, float>) return "<f4";
    else if constexpr (std::is_same_v<T, double>) return "<f8";
    else if constexpr (std::is_same_v<T, uint32_t>) return "<u4";
    else if constexpr (std::is_same_v<T, int16_t>) return "<i2";
    else if constexpr (std::is_same_v<T, uint8_t>) return "|u1";
    else static_assert(sizeof(T) == 0, "unsupported dtype");
}

std::string field(const std::string& header, const std::string& key) {
    auto at = header.find("'" + key + "'");
    if (at == std::string::npos) throw std::runtime_error("npy header without " + key);
    auto colon = header.find(':', at);
    auto start = header.find_first_not_of(' ', colon + 1);
    char open = header[start];
    size_t end;
    if (open == '\'') {
        end = header.find('\'', start + 1);
        return header.substr(start + 1, end - start - 1);
    }
    if (open == '(') {
        end = header.find(')', start);
        return header.substr(start + 1, end - start - 1);
    }
    end = header.find_first_of(",}", start);
    return header.substr(start, end - start);
}

}  // namespace

template <typename T>
Array<T> load_npy(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot open " + path);
    char magic[8];
    in.read(magic, 8);
    if (std::memcmp(magic, "\x93NUMPY", 6) != 0) throw std::runtime_error(path + ": not a .npy file");
    size_t header_len = 0;
    if (magic[6] == 1) {
        uint16_t len;
        in.read(reinterpret_cast<char*>(&len), 2);
        header_len = len;
    } else {
        uint32_t len;
        in.read(reinterpret_cast<char*>(&len), 4);
        header_len = len;
    }
    std::string header(header_len, '\0');
    in.read(header.data(), static_cast<std::streamsize>(header_len));

    if (field(header, "descr") != descr<T>())
        throw std::runtime_error(path + ": dtype " + field(header, "descr") + ", expected " + descr<T>());
    const bool fortran = field(header, "fortran_order") == "True";

    Array<T> array;
    size_t count = 1;
    std::string dims = field(header, "shape");
    size_t pos = 0;
    while (pos < dims.size()) {
        auto comma = dims.find(',', pos);
        std::string part = dims.substr(pos, comma == std::string::npos ? std::string::npos : comma - pos);
        auto first = part.find_first_not_of(' ');
        if (first != std::string::npos) {
            size_t n = std::stoull(part.substr(first));
            array.shape.push_back(n);
            count *= n;
        }
        if (comma == std::string::npos) break;
        pos = comma + 1;
    }
    array.data.resize(count);
    in.read(reinterpret_cast<char*>(array.data.data()), static_cast<std::streamsize>(count * sizeof(T)));
    if (!in) throw std::runtime_error(path + ": truncated");
    if (fortran && array.shape.size() == 2) {  // column-major (e.g. a transposed array): reorder
        const size_t rows = array.shape[0], cols = array.shape[1];
        std::vector<T> c_order(count);
        for (size_t r = 0; r < rows; ++r)
            for (size_t c = 0; c < cols; ++c) c_order[r * cols + c] = array.data[c * rows + r];
        array.data.swap(c_order);
    } else if (fortran && array.shape.size() > 2) {
        throw std::runtime_error(path + ": Fortran order with more than 2 dimensions");
    }
    return array;
}

template Array<float> load_npy<float>(const std::string&);
template Array<double> load_npy<double>(const std::string&);
template Array<uint32_t> load_npy<uint32_t>(const std::string&);
template Array<int16_t> load_npy<int16_t>(const std::string&);
template Array<uint8_t> load_npy<uint8_t>(const std::string&);

}  // namespace chordchart
