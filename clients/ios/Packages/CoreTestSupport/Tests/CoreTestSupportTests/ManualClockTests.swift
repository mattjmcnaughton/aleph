import CoreTestSupport
import Testing

struct ManualClockTests {
    @Test func aSleeperWakesOnlyWhenTheClockPassesItsDeadline() async throws {
        let clock = ManualClock()
        let woke = LockedBox(false)
        let sleeper = Task {
            try await clock.sleep(for: .seconds(2))
            woke.value = true
        }
        while clock.sleeperCount == 0 {
            await Task.yield()
        }

        await clock.advance(by: .seconds(1))
        #expect(!woke.value)

        await clock.advance(by: .seconds(1))
        try await sleeper.value
        #expect(woke.value)
    }

    @Test func aDeadlineAlreadyPassedDoesNotSleep() async throws {
        let clock = ManualClock()
        await clock.advance(by: .seconds(5))

        try await clock.sleep(until: clock.now.advanced(by: .seconds(-1)))

        #expect(clock.sleeperCount == 0)
    }

    @Test func cancellingASleeperThrowsCancellationError() async {
        let clock = ManualClock()
        let sleeper = Task {
            try await clock.sleep(for: .seconds(60))
        }
        while clock.sleeperCount == 0 {
            await Task.yield()
        }

        sleeper.cancel()

        await #expect(throws: CancellationError.self) {
            try await sleeper.value
        }
        #expect(clock.sleeperCount == 0)
    }
}
