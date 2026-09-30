plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// The models and tables the analysis loads aren't in git: tools/prepare_assets.py generates
// them (deterministically) into src/main/assets/analysis.
val checkAnalysisAssets = tasks.register("checkAnalysisAssets") {
    val dir = file("src/main/assets/analysis")
    doLast {
        val needed = listOf("chord_features.onnx", "key.onnx", "beat_this_small0.onnx", "settings.json", "crf_W.npy", "dbn4_states.npy")
        val missing = needed.filterNot { File(dir, it).isFile }
        if (missing.isNotEmpty()) {
            throw GradleException(
                "Missing analysis assets (${missing.joinToString()}). Generate them first:\n" +
                    "    uv run --with onnx --with onnxscript python android/tools/prepare_assets.py"
            )
        }
    }
}

// The full licence texts of Android's MP3 and AAC decoders (built into the core), from the
// fetched sources: shipped with the app, shown on its About screen.
val decoderLicenses = layout.buildDirectory.dir("generated/licenses")
val copyDecoderLicenses = tasks.register<Copy>("copyDecoderLicenses") {
    val thirdParty = rootProject.file("third_party")
    into(decoderLicenses.map { it.dir("licenses") })
    from(File(thirdParty, "aosp-aac/NOTICE")) { rename { "Fraunhofer FDK AAC.txt" } }
    from(File(thirdParty, "aosp-mp3dec/NOTICE")) { rename { "Android Open Source Project (Apache-2.0).txt" } }
    from(File(thirdParty, "aosp-mp3dec/patent_disclaimer.txt")) { rename { "Android codecs patent disclaimer.txt" } }
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
            assets.directories.add(decoderLicenses.get().asFile.path)
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

tasks.named("preBuild") { dependsOn(checkAnalysisAssets, copyDecoderLicenses) }

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.09.00")
    implementation(composeBom)
    implementation("androidx.activity:activity-compose:1.12.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.10.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.10.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.core:core-ktx:1.17.0")
}
