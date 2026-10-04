import java.util.Properties

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
    from(rootProject.file("LICENSE")) { rename { "GNU General Public License 3.0.txt" } }
    from(File(thirdParty, "aosp-aac/NOTICE")) { rename { "Fraunhofer FDK AAC.txt" } }
    from(File(thirdParty, "aosp-mp3dec/NOTICE")) { rename { "Android Open Source Project (Apache-2.0).txt" } }
    from(File(thirdParty, "aosp-mp3dec/patent_disclaimer.txt")) { rename { "Android codecs patent disclaimer.txt" } }
}

// Release signing: a keystore of your own, so every release APK can be installed over the
// last one. Read from android/keystore.properties (git-ignored; see android/README.md) or,
// on CI, from CHORDCHART_KEYSTORE_* environment variables. Without either, the release
// build is signed with this machine's debug key: installable, but a different machine's
// build can't be installed over it.
val keystoreProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.isFile) f.inputStream().use { load(it) }
}
fun signingValue(prop: String, env: String): String? =
    keystoreProps.getProperty(prop) ?: System.getenv(env)?.takeIf { it.isNotEmpty() }
val releaseStoreFile = signingValue("storeFile", "CHORDCHART_KEYSTORE_FILE")

android {
    namespace = "io.github.shanikn.chordchart"
    compileSdk = 37
    ndkVersion = "29.0.14206865"

    defaultConfig {
        applicationId = "io.github.shanikn.chordchart"
        minSdk = 24  // Android 7.0; ONNX Runtime 1.30 is built for API 24 too
        targetSdk = 35
        // CI passes -PversionCode=<run number>, so each build installs over the previous one.
        versionCode = (findProperty("versionCode") as String?)?.toInt() ?: 1
        versionName = (findProperty("versionName") as String?) ?: "0.3.0"  // CI: from a v* tag
        externalNativeBuild {
            cmake {
                arguments += listOf("-DANDROID_STL=c++_shared")
            }
        }
    }

    compileOptions {
        isCoreLibraryDesugaringEnabled = true
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

    signingConfigs {
        if (releaseStoreFile != null) {
            create("release") {
                storeFile = rootProject.file(releaseStoreFile)
                storePassword = signingValue("storePassword", "CHORDCHART_KEYSTORE_PASSWORD")
                keyAlias = signingValue("keyAlias", "CHORDCHART_KEY_ALIAS")
                keyPassword = signingValue("keyPassword", "CHORDCHART_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        getByName("debug") {
            // real phones, and the x86_64 emulator
            ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
        }
        getByName("release") {
            // real phones only: the APK is about half the size without the emulator's ABI
            ndk { abiFilters += listOf("arm64-v8a") }
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
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
    // YouTube and Spotify links (GPL-3.0; the commit NewPipe itself builds with)
    implementation("com.github.TeamNewPipe:NewPipeExtractor:13a655fe53e0c3065f88725fc1fb594c3ede0169")
    // java.time and java.nio for NewPipe Extractor on Android 7
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs_nio:2.1.5")
}
