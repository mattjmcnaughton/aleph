import Foundation

/// The learner's self-assessed starting point for a path (docs/CONTEXT.md: Level).
enum Level: String {
    case newToIt = "New to it"
    case someExperience = "Some experience"
    case iWorkInIt = "I work in it"
}

/// A stub of a learner's path for the home screen. Hard-coded; no networking.
struct SamplePath: Identifiable {
    let id: String
    let title: String
    let level: Level
    let lessonsComplete: Int
    let lessonCount: Int

    var progress: Double {
        lessonCount == 0 ? 0 : Double(lessonsComplete) / Double(lessonCount)
    }

    static let samples: [SamplePath] = [
        SamplePath(id: "rust", title: "Rust ownership", level: .someExperience,
                   lessonsComplete: 7, lessonCount: 12),
        SamplePath(id: "healthcare", title: "How US healthcare is paid for", level: .newToIt,
                   lessonsComplete: 2, lessonCount: 10),
        SamplePath(id: "postgres", title: "Postgres query planning", level: .iWorkInIt,
                   lessonsComplete: 9, lessonCount: 9),
    ]
}
