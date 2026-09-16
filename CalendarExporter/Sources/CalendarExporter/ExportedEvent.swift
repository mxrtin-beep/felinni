import CoreGraphics
import Foundation

/// One calendar event, normalized into the JSON schema the Python analysis
/// side (analysis/felinni/ingest.py) expects.
struct ExportedEvent: Codable {
    var id: String
    var title: String
    var notes: String?
    var location: String?
    var startDate: Date
    var endDate: Date
    var isAllDay: Bool
    var calendarTitle: String
    var calendarColorHex: String?
    var attendees: [String]
    var isRecurring: Bool
    var url: String?

    /// Freeform "Key: value, value" lines pulled out of `notes`, e.g.
    ///   People: Alice, Bob
    ///   Category: Gym
    /// so events tagged by hand in the Notes field still parse cleanly even
    /// when the title/location fields don't carry that info.
    var noteTags: [String: [String]]
}

enum CalendarColor {
    /// Converts a Calendar's `cgColor` (whatever the user picked for that
    /// calendar in Calendar.app) to a plain "#RRGGBB" hex string, so the
    /// dashboard can color categories/map markers/habit charts to match
    /// the same colors the user already associates with each calendar,
    /// rather than an arbitrary fixed palette. `cgColor` is used (over the
    /// AppKit `NSColor`/UIKit `UIColor` typed accessor) since it's the same
    /// API on macOS and iOS.
    static func hex(from cgColor: CGColor) -> String? {
        guard let converted = cgColor.converted(to: CGColorSpaceCreateDeviceRGB(), intent: .defaultIntent, options: nil),
              let components = converted.components, components.count >= 3
        else { return nil }
        let r = Int((components[0] * 255).rounded())
        let g = Int((components[1] * 255).rounded())
        let b = Int((components[2] * 255).rounded())
        return String(format: "#%02X%02X%02X", r, g, b)
    }
}

enum NoteTagParser {
    /// Recognized "Key: v1, v2" lines. Keys are matched case-insensitively.
    static func parse(_ notes: String?) -> [String: [String]] {
        guard let notes else { return [:] }
        var result: [String: [String]] = [:]
        for rawLine in notes.split(separator: "\n") {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            guard let colonIndex = line.firstIndex(of: ":") else { continue }
            let key = line[line.startIndex..<colonIndex]
                .trimmingCharacters(in: .whitespaces)
                .lowercased()
            guard !key.isEmpty, key.count < 32 else { continue }
            let valuePart = line[line.index(after: colonIndex)...]
            let values = valuePart
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
            guard !values.isEmpty else { continue }
            result[key, default: []].append(contentsOf: values)
        }
        return result
    }
}
