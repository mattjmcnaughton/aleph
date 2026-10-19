import Core
import Testing

/// The home greeting's name. Mirrors `routes/index.tsx` (`display_name.split(" ")[0]`) and
/// `clients/web/src/app/app.test.tsx` ("Welcome back, Dev.").
struct AccountTests {
    @Test(arguments: [
        ("Dev Learner", "Dev"),
        ("Ada", "Ada"),
        ("Mary Ann Evans", "Mary"),
        (" Leading", ""),
        ("", ""),
    ])
    func firstNameIsTheDisplayNameUpToItsFirstSpace(displayName: String, expected: String) {
        let account = Account(
            id: "u-1",
            username: "dev",
            displayName: displayName,
            email: nil,
            isAdmin: false,
            featureFlags: .allOff
        )
        #expect(account.firstName == expected)
    }
}
