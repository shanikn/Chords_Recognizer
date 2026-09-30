// Stand-in for Android's internal <log/log.h> (not in the NDK), for the FDK AAC decoder:
// it only uses it for SafetyNet error logging, which the app doesn't need.
#pragma once
#define android_errorWriteLog(tag, subtag) ((void)0)
