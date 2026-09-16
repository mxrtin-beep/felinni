import EventKit
import Foundation

enum CalendarAccessError: Error, CustomStringConvertible {
    case denied
    case unknown(Error)

    var description: String {
        switch self {
        case .denied:
            return "Calendar access was denied. Grant it in System Settings > Privacy & Security > Calendars."
        case .unknown(let error):
            return "Calendar access request failed: \(error)"
        }
    }
}

enum CalendarAccess {
    /// Bridges EventKit's completion-handler / async access APIs into a
    /// synchronous call, since this is a plain CLI tool without a run loop
    /// driving Swift concurrency for us.
    static func requestAccess(to store: EKEventStore) throws {
        let semaphore = DispatchSemaphore(value: 0)
        var grantedResult: Bool = false
        var caughtError: Error?

        if #available(macOS 14.0, *) {
            Task {
                do {
                    grantedResult = try await store.requestFullAccessToEvents()
                } catch {
                    caughtError = error
                }
                semaphore.signal()
            }
        } else {
            store.requestAccess(to: .event) { granted, error in
                grantedResult = granted
                caughtError = error
                semaphore.signal()
            }
        }

        semaphore.wait()

        if let caughtError {
            throw CalendarAccessError.unknown(caughtError)
        }
        if !grantedResult {
            throw CalendarAccessError.denied
        }
    }
}
