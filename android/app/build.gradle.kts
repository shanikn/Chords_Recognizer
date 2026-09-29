plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// The Beat This! model lives in the repository once (chordchart/models); it's copied into
// the app's assets at build time (git-ignored there), next to the tables and madmom's CNNs.
val copyBeatThis = tasks.register<Copy>("copyBeatThis") {
    from(rootProject.file("../chordchart/models/beat_this_small0.onnx"))
    into(file("src/main/assets/analysis"))
}

android {
    namespace = "io.github.shanikn.chordchart"
    compileSdk = 37
    ndkVersion = "29.0.14206865"

    defaultConfig {
        applicationId = "io.github.shanikn.chordchart"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        ndk {
            // real phones, and the x86_64 emulator
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
        externalNativeBuild {
            cmake {
                arguments += listOf("-DANDROID_STL=c++_shared")
            }
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.31.6"
        }
    }

    sourceSets {
        getByName("main") {
            // ONNX Runtime's prebuilt libonnxruntime.so per ABI (tools/fetch_deps.py)
            jniLibs.directories.add(rootProject.file("third_party/onnxruntime-android/jni").path)
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
        }
    }

    buildFeatures {
        compose = true
    }

    packaging {
        jniLibs {
            useLegacyPackaging = false
        }
    }

    androidResources {
        // The models and .npy tables are read from files; keep them uncompressed in the APK.
        noCompress += listOf("onnx", "npy")
    }
}

tasks.named("preBuild") { dependsOn(copyBeatThis) }

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.09.00")
    implementation(composeBom)
    implementation("androidx.activity:activity-compose:1.12.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.core:core-ktx:1.17.0")
}
