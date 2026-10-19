/// Every failure a screen can see (iOS TDD §4.4). The API adapters map the shared error
/// envelope's status, `URLError` and auth failures to these cases; screens never see
/// `URLError` or a generated type.
public enum AppError: Error, Equatable, Sendable {
    /// No network (`URLError.notConnectedToInternet`, `.networkConnectionLost`).
    case offline
    case timedOut
    /// The token refresh failed: the session is over and the app signs out.
    case unauthenticated
    /// `404`. Never "forbidden": the API does not disclose existence.
    case notFound
    /// `409`, with the envelope's `details.reason` when it has one.
    case conflict(reason: String?)
    /// `429`: the daily limit, nothing to retry.
    case rateLimited
    /// `422`.
    case validation(message: String)
    /// `503`: the identity provider cannot be reached.
    case serviceUnavailable
    /// Any other status; `0` when there was no response at all.
    case server(status: Int)
}
