#include "json.hpp"

#include <charconv>
#include <cmath>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace chordchart::json {

namespace {

struct Parser {
    const std::string& s;
    size_t i = 0;

    void ws() {
        while (i < s.size() && (s[i] == ' ' || s[i] == '\n' || s[i] == '\r' || s[i] == '\t')) ++i;
    }
    [[noreturn]] void fail(const char* what) {
        throw std::runtime_error(std::string("JSON: ") + what + " at " + std::to_string(i));
    }
    void expect(char c) {
        ws();
        if (i >= s.size() || s[i] != c) fail("unexpected character");
        ++i;
    }
    bool literal(const char* word) {
        size_t n = std::char_traits<char>::length(word);
        if (s.compare(i, n, word) == 0) {
            i += n;
            return true;
        }
        return false;
    }

    std::string string() {
        expect('"');
        std::string out;
        while (i < s.size() && s[i] != '"') {
            char c = s[i++];
            if (c != '\\') {
                out += c;
                continue;
            }
            char e = s[i++];
            switch (e) {
                case 'n': out += '\n'; break;
                case 't': out += '\t'; break;
                case 'r': out += '\r'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'u': {
                    unsigned code = std::stoul(s.substr(i, 4), nullptr, 16);
                    i += 4;
                    if (code >= 0xD800 && code <= 0xDBFF && s.compare(i, 2, "\\u") == 0) {
                        unsigned low = std::stoul(s.substr(i + 2, 4), nullptr, 16);
                        i += 6;
                        code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                    }
                    if (code < 0x80) out += static_cast<char>(code);
                    else if (code < 0x800) {
                        out += static_cast<char>(0xC0 | (code >> 6));
                        out += static_cast<char>(0x80 | (code & 0x3F));
                    } else if (code < 0x10000) {
                        out += static_cast<char>(0xE0 | (code >> 12));
                        out += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
                        out += static_cast<char>(0x80 | (code & 0x3F));
                    } else {
                        out += static_cast<char>(0xF0 | (code >> 18));
                        out += static_cast<char>(0x80 | ((code >> 12) & 0x3F));
                        out += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
                        out += static_cast<char>(0x80 | (code & 0x3F));
                    }
                    break;
                }
                default: out += e;
            }
        }
        ++i;
        return out;
    }

    Value value() {
        ws();
        if (i >= s.size()) fail("unexpected end");
        char c = s[i];
        if (c == '{') {
            ++i;
            Object o;
            ws();
            if (s[i] == '}') {
                ++i;
                return o;
            }
            while (true) {
                std::string key = string();
                expect(':');
                o.emplace(std::move(key), value());
                ws();
                if (s[i] == ',') {
                    ++i;
                    continue;
                }
                expect('}');
                return o;
            }
        }
        if (c == '[') {
            ++i;
            List l;
            ws();
            if (s[i] == ']') {
                ++i;
                return l;
            }
            while (true) {
                l.push_back(value());
                ws();
                if (s[i] == ',') {
                    ++i;
                    continue;
                }
                expect(']');
                return l;
            }
        }
        if (c == '"') return string();
        if (literal("true")) return true;
        if (literal("false")) return false;
        if (literal("null")) return nullptr;
        if (literal("NaN")) return std::nan("");
        if (literal("Infinity")) return HUGE_VAL;
        if (literal("-Infinity")) return -HUGE_VAL;
        size_t start = i;
        while (i < s.size() && (std::isdigit(static_cast<unsigned char>(s[i])) || s[i] == '-' ||
                                s[i] == '+' || s[i] == '.' || s[i] == 'e' || s[i] == 'E'))
            ++i;
        double d = 0;
        auto result = std::from_chars(s.data() + start, s.data() + i, d);
        if (result.ec != std::errc()) fail("bad number");
        return d;
    }
};

void write_string(std::string& out, const std::string& s) {
    out += '"';
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof buf, "\\u%04x", c);
                    out += buf;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    out += '"';
}

void write(std::string& out, const Value& value) {
    if (value.is_null()) {
        out += "null";
    } else if (auto b = std::get_if<bool>(&value.v)) {
        out += *b ? "true" : "false";
    } else if (auto d = std::get_if<double>(&value.v)) {
        if (std::isnan(*d)) {
            out += "NaN";
        } else if (std::isinf(*d)) {
            out += *d > 0 ? "Infinity" : "-Infinity";
        } else {
            char buf[32];
            auto result = std::to_chars(buf, buf + sizeof buf, *d);  // shortest round-trip
            out.append(buf, result.ptr);
        }
    } else if (auto s = std::get_if<std::string>(&value.v)) {
        write_string(out, *s);
    } else if (auto l = std::get_if<List>(&value.v)) {
        out += '[';
        for (size_t i = 0; i < l->size(); ++i) {
            if (i) out += ',';
            write(out, (*l)[i]);
        }
        out += ']';
    } else {
        const auto& o = std::get<Object>(value.v);
        out += '{';
        bool first = true;
        for (const auto& [key, item] : o) {
            if (!first) out += ',';
            first = false;
            write_string(out, key);
            out += ':';
            write(out, item);
        }
        out += '}';
    }
}

}  // namespace

Value parse(const std::string& text) {
    Parser p{text};
    return p.value();
}

Value parse_file(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot open " + path);
    std::stringstream buffer;
    buffer << in.rdbuf();
    return parse(buffer.str());
}

std::string dump(const Value& value) {
    std::string out;
    write(out, value);
    return out;
}

}  // namespace chordchart::json
