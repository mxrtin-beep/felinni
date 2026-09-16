// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "CalendarExporter",
    platforms: [.macOS(.v12)],
    targets: [
        .executableTarget(
            name: "CalendarExporter",
            path: "Sources/CalendarExporter"
        )
    ]
)
