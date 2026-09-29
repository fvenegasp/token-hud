// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CuotaBar",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "CuotaBar", targets: ["CuotaBar"]),
    ],
    targets: [
        // Lógica pura (modelo del contrato v1 + Presenter). Solo Foundation.
        .target(name: "CuotaCore"),
        // App de barra de menú (AppKit).
        .executableTarget(
            name: "CuotaBar",
            dependencies: ["CuotaCore"],
            resources: [.copy("Resources/logos")]
        ),
        .testTarget(name: "CuotaCoreTests", dependencies: ["CuotaCore"]),
    ]
)
