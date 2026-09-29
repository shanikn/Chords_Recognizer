// A small JSON value, parser and writer: enough for settings.json, the golden files'
// stages.json, and the Song output. Numbers are doubles.

#pragma once

#include <map>
#include <memory>
#include <string>
#include <variant>
#include <vector>

namespace chordchart::json {

struct Value;
using Object = std::map<std::string, Value>;
using List = std::vector<Value>;

struct Value {
    std::variant<std::nullptr_t, bool, double, std::string, List, Object> v;

    Value() : v(nullptr) {}
    Value(std::nullptr_t) : v(nullptr) {}
    Value(bool b) : v(b) {}
    Value(double d) : v(d) {}
    Value(int i) : v(static_cast<double>(i)) {}
    Value(std::string s) : v(std::move(s)) {}
    Value(const char* s) : v(std::string(s)) {}
    Value(List l) : v(std::move(l)) {}
    Value(Object o) : v(std::move(o)) {}

    bool is_null() const { return std::holds_alternative<std::nullptr_t>(v); }
    double num() const { return std::get<double>(v); }
    bool boolean() const { return std::get<bool>(v); }
    const std::string& str() const { return std::get<std::string>(v); }
    const List& list() const { return std::get<List>(v); }
    const Object& obj() const { return std::get<Object>(v); }
    const Value& operator[](const std::string& key) const { return obj().at(key); }
    const Value& operator[](size_t i) const { return list().at(i); }
};

Value parse(const std::string& text);
Value parse_file(const std::string& path);

// Compact JSON; doubles written with 17 significant digits, like Python's repr, so
// values survive a round trip exactly.
std::string dump(const Value& value);

}  // namespace chordchart::json
