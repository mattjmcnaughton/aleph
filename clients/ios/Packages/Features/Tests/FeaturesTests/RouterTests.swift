import Core
import Foundation
import Testing

@testable import Features

struct RouterTests {
    private let path = PathID(rawValue: "p-1")

    @Test func pushAppendsAndPopToRootEmpties() {
        let router = Router()

        router.push(.path(path))
        router.push(.lesson(LessonID(rawValue: "l-1")))
        #expect(router.stack == [.path(path), .lesson(LessonID(rawValue: "l-1"))])

        router.popToRoot()
        #expect(router.stack.isEmpty)
    }

    @Test func replaceSwapsTheWholeStack() {
        let router = Router()
        router.push(.flowReceipt)

        router.replace(with: [.path(path)])

        #expect(router.stack == [.path(path)])
    }

    @Test func routesRoundTripThroughCodable() throws {
        let routes: [Route] = [.path(path), .newPath(topic: nil), .flowSetup(preselect: path), .flowReceipt]

        let decoded = try JSONDecoder().decode([Route].self, from: JSONEncoder().encode(routes))

        #expect(decoded == routes)
    }
}
