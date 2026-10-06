#pragma once

#include <cstdio>
#include <sstream>
#include <string>

namespace robo_claw_core
{

/// JSON 문자열 이스케이프 (표준 RFC 8259)
inline std::string json_escape(const std::string & value)
{
  std::ostringstream oss;
  for (char ch : value) {
    switch (ch) {
      case '"':  oss << "\\\""; break;
      case '\\': oss << "\\\\"; break;
      case '\b': oss << "\\b";  break;
      case '\f': oss << "\\f";  break;
      case '\n': oss << "\\n";  break;
      case '\r': oss << "\\r";  break;
      case '\t': oss << "\\t";  break;
      default:
        if (static_cast<unsigned char>(ch) < 0x20) {
          // 제어 문자는 \uXXXX 형태로 이스케이프
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", ch);
          oss << buf;
        } else {
          oss << ch;
        }
        break;
    }
  }
  return oss.str();
}

/// JSON 문자열 필드 조각 생성 ("key":"escaped_value")
inline std::string json_string_field(const std::string & key, const std::string & value)
{
  std::ostringstream oss;
  oss << "\"" << json_escape(key) << "\":\"" << json_escape(value) << "\"";
  return oss.str();
}

/// JSON 불리언 필드 조각 생성 ("key":true|false)
inline std::string json_bool_field(const std::string & key, bool value)
{
  std::ostringstream oss;
  oss << "\"" << json_escape(key) << "\":" << (value ? "true" : "false");
  return oss.str();
}

/// JSON 숫자 필드 조각 생성 ("key":value)
template<typename T>
inline std::string json_number_field(const std::string & key, T value)
{
  std::ostringstream oss;
  oss << "\"" << json_escape(key) << "\":" << value;
  return oss.str();
}

}  // namespace robo_claw_core