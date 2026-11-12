import org.jetbrains.kotlin.gradle.tasks.KotlinCompile

plugins {
    kotlin("jvm") version "1.9.23"
    kotlin("plugin.serialization") version "1.9.23"
    `java-library`
    `maven-publish`
}

group = "com.liuhao.sdk"
version = "0.1.0"

repositories {
    mavenCentral()
}

/**
 * LiuHao AI OS Kotlin SDK
 *
 * 依赖版本统一管理，便于后续升级。
 */
object Versions {
    const val ktor = "2.3.10"
    const val kotlinxCoroutines = "1.8.0"
    const val kotlinxSerialization = "1.6.3"
    const val slf4j = "2.0.12"
    const val kotlinLogging = "6.0.3"
}

dependencies {
    // ---------------------------------------------------------------------
    // HTTP 客户端：Ktor Client + CIO 引擎
    // ---------------------------------------------------------------------
    api("io.ktor:ktor-client-core:${Versions.ktor}")
    api("io.ktor:ktor-client-cio:${Versions.ktor}")
    api("io.ktor:ktor-client-content-negotiation:${Versions.ktor}")
    api("io.ktor:ktor-serialization-kotlinx-json:${Versions.ktor}")
    api("io.ktor:ktor-client-auth:${Versions.ktor}")
    api("io.ktor:ktor-client-logging:${Versions.ktor}")

    // ---------------------------------------------------------------------
    // Kotlin 协程
    // ---------------------------------------------------------------------
    api("org.jetbrains.kotlinx:kotlinx-coroutines-core:${Versions.kotlinxCoroutines}")
    api("org.jetbrains.kotlinx:kotlinx-coroutines-slf4j:${Versions.kotlinxCoroutines}")

    // ---------------------------------------------------------------------
    // 序列化
    // ---------------------------------------------------------------------
    api("org.jetbrains.kotlinx:kotlinx-serialization-json:${Versions.kotlinxSerialization}")

    // ---------------------------------------------------------------------
    // 日志门面（仅编译期，运行时由使用方提供实现）
    // ---------------------------------------------------------------------
    compileOnly("org.slf4j:slf4j-api:${Versions.slf4j}")

    // ---------------------------------------------------------------------
    // 测试
    // ---------------------------------------------------------------------
    testImplementation(kotlin("test"))
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:${Versions.kotlinxCoroutines}")
    testImplementation("io.ktor:ktor-client-mock:${Versions.ktor}")
}

tasks.withType<KotlinCompile>().configureEach {
    kotlinOptions {
        jvmTarget = "17"
        freeCompilerArgs = freeCompilerArgs + listOf(
            "-Xjvm-default=all",
            "-opt-in=kotlin.RequiresOptIn"
        )
    }
}

java {
    withSourcesJar()
    withJavadocJar()
}

tasks.test {
    useJUnitPlatform()
    testLogging {
        events("passed", "skipped", "failed")
        showStandardStreams = true
    }
}

publishing {
    publications {
        create<MavenPublication>("maven") {
            from(components["java"])
            pom {
                name.set("LiuHao AI OS Kotlin SDK")
                description.set("Official Kotlin client for the LiuHao AI OS platform")
                url.set("https://github.com/liuhao-ai/liuhao-sdk-kotlin")
                licenses {
                    license {
                        name.set("Apache License 2.0")
                        url.set("https://www.apache.org/licenses/LICENSE-2.0")
                    }
                }
            }
        }
    }
}
