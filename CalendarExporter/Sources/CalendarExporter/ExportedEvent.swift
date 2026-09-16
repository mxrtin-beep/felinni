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
