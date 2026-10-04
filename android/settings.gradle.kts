pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // NewPipe Extractor and its JSON parser are published only there
        maven("https://jitpack.io") {
            content { includeGroup("com.github.TeamNewPipe") }
        }
    }
}
rootProject.name = "ChordChart"
include(":app")
