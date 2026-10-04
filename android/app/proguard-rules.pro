# The native core (app/src/main/cpp/jni.cpp) reaches back into Kotlin by name; R8 must
# keep those names. Native methods themselves are kept by proguard-android-optimize.txt.

# jni.cpp throws it with FindClass + ThrowNew (needs the String constructor).
-keep class io.github.shanikn.chordchart.AnalysisException { <init>(java.lang.String); }

# jni.cpp calls listener.onProgress(String, double) via GetMethodID.
-keep interface io.github.shanikn.chordchart.ProgressListener { *; }
-keepclassmembers class * implements io.github.shanikn.chordchart.ProgressListener {
    void onProgress(java.lang.String, double);
}
