// MSVC only (the PC tests): AOSP builds the MP3/AAC decoders with clang; drop GCC attributes.
#pragma once
#define __attribute__(x)
