import EventKit
import Foundation

struct Options {
    var startDate: Date
    var endDate: Date
    var outputPath: String
    var calendarNames: [String]?
}

func parseDate(_ s: String, label: String) -> Date {
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyy-MM-dd"
    formatter.timeZone = .current
    guard let date = formatter.date(from: s) else {
        FileHandle.standardError.write("Invalid \(label) date '\(s)', expected yyyy-MM-dd\n".data(using: .utf8)!)
        exit(1)
    }
    return date
}

func parseArgs() -> Options {
    var args = CommandLine.arguments.dropFirst()
    var startDate = Calendar.current.date(byAdding: .year, value: -15, to: Date())!
    var endDate = Date()
    var outputPath = "events.json"
    var calendarNames: [String]?

    func nextValue(_ flag: String) -> String {
        guard let value = args.first else {
            FileHandle.standardError.write("Missing value for \(flag)\n".data(using: .utf8)!)
            exit(1)
        }
        args = args.dropFirst()
        return value
    }

    while let arg = args.first {
        args = args.dropFirst()
        switch arg {
        case "--start":
            startDate = parseDate(nextValue(arg), label: "start")
        case "--end":
            endDate = parseDate(nextValue(arg), label: "end")
        case "--output", "-o":
            outputPath = nextValue(arg)
        case "--calendars":
            calendarNames = nextValue(arg).split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
        case "--help", "-h":
            print("""
            Usage: CalendarExporter [--start yyyy-MM-dd] [--end yyyy-MM-dd] [--output events.json] [--calendars "Gym,Social,Dating"]

            Exports EventKit calendar events into a normalized JSON file for the
            felinni Python analysis toolkit. Defaults to the last 15 years, all
            calendars, writing to ./events.json.
            """)
            exit(0)
        default:
            FileHandle.standardError.write("Unknown argument: \(arg)\n".data(using: .utf8)!)
            exit(1)
        }
    }

    return Options(startDate: startDate, endDate: endDate, outputPath: outputPath, calendarNames: calendarNames)
}

let options = parseArgs()
let store = EKEventStore()

do {
    try CalendarAccess.requestAccess(to: store)
} catch {
    FileHandle.standardError.write("\(error)\n".data(using: .utf8)!)
    exit(1)
}

var calendars: [EKCalendar]? = nil
if let wanted = options.calendarNames {
    let all = store.calendars(for: .event)
    calendars = all.filter { wanted.contains($0.title) }
    if calendars?.isEmpty ?? true {
        FileHandle.standardError.write("No calendars matched \(wanted). Available: \(all.map(\.title))\n".data(using: .utf8)!)
        exit(1)
    }
}

// EventKit predicates cap the queryable span (typically ~4 years on some OS
// versions), so we chunk the request range into yearly windows and merge.
var allEvents: [EKEvent] = []
var windowStart = options.startDate
let calendar = Calendar.current
while windowStart < options.endDate {
    let windowEnd = min(calendar.date(byAdding: .year, value: 1, to: windowStart)!, options.endDate)
    let predicate = store.predicateForEvents(withStart: windowStart, end: windowEnd, calendars: calendars)
    allEvents.append(contentsOf: store.events(matching: predicate))
    windowStart = windowEnd
}

// Recurring events produce one EKEvent occurrence per instance sharing an
// eventIdentifier; keep every occurrence but dedupe exact overlapping fetches
// from window-boundary double counting.
var seen = Set<String>()
var exported: [ExportedEvent] = []
for event in allEvents {
    let key = "\(event.eventIdentifier ?? UUID().uuidString)|\(event.startDate.timeIntervalSince1970)"
    guard seen.insert(key).inserted else { continue }

    let attendeeNames: [String] = (event.attendees ?? []).compactMap { participant in
        participant.name ?? participant.url.absoluteString
    }

    exported.append(ExportedEvent(
        id: event.eventIdentifier ?? UUID().uuidString,
        title: event.title ?? "",
        notes: event.notes,
        location: event.location,
        startDate: event.startDate,
        endDate: event.endDate,
        isAllDay: event.isAllDay,
        calendarTitle: event.calendar.title,
        attendees: attendeeNames,
        isRecurring: event.hasRecurrenceRules,
        url: event.url?.absoluteString,
        noteTags: NoteTagParser.parse(event.notes)
    ))
}

exported.sort { $0.startDate < $1.startDate }

let encoder = JSONEncoder()
encoder.dateEncodingStrategy = .iso8601
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]

do {
    let data = try encoder.encode(exported)
    try data.write(to: URL(fileURLWithPath: options.outputPath))
    print("Exported \(exported.count) events to \(options.outputPath)")
} catch {
    FileHandle.standardError.write("Failed to write output: \(error)\n".data(using: .utf8)!)
    exit(1)
}
