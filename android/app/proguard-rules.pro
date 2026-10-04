# The native core (app/src/main/cpp/jni.cpp) reaches back into Kotlin by name; R8 must
# keep those names. Native methods themselves are kept by proguard-android-optimize.txt.

# jni.cpp throws it with FindClass + ThrowNew (needs the String constructor).
-keep class io.github.shanikn.chordchart.AnalysisException { <init>(java.lang.String); }

# jni.cpp calls listener.onProgress(String, double) via GetMethodID.
-keep interface io.github.shanikn.chordchart.ProgressListener { *; }
-keepclassmembers class * implements io.github.shanikn.chordchart.ProgressListener {
    void onProgress(java.lang.String, double);
}

# NewPipe Extractor (YouTube and Spotify links), as NewPipe's own app keeps it. Rhino runs
# YouTube's player JavaScript and loads parts of itself by reflection.
-keep class org.schabi.newpipe.extractor.** { *; }
-keep class org.mozilla.javascript.** { *; }
-keep class org.mozilla.classfile.ClassFileWriter
-dontwarn org.mozilla.javascript.JavaToJSONConverters
-dontwarn org.mozilla.javascript.tools.**
-keep class javax.script.** { *; }
-dontwarn javax.script.**
-keep class jdk.dynalink.** { *; }
-dontwarn jdk.dynalink.**
-keepclassmembers class * extends com.google.protobuf.GeneratedMessageLite { <fields>; }
-dontwarn javax.annotation.**
-dontwarn com.google.errorprone.annotations.**
-dontwarn java.beans.**
