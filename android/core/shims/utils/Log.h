// Stand-in for Android's internal <utils/Log.h>, for Codec2's DRC helper (DrcPresModeWrap):
// its logging is verbose-level only, compiled out in release builds of Android too.
#pragma once
#define ALOGV(...) ((void)0)
#define ALOGD(...) ((void)0)
#define ALOGI(...) ((void)0)
#define ALOGW(...) ((void)0)
#define ALOGE(...) ((void)0)
