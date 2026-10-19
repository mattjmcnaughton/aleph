internal import Core
internal import Foundation
internal import OpenAPIRuntime

#if canImport(FoundationNetworking)
    internal import FoundationNetworking
#endif

/// Maps transport failures and the shared error envelope to `AppError` (iOS TDD §4.4), so
/// screens never see `URLError`, `ClientError` or a generated type.
enum ErrorMapping {
    /// A non-2xx response. `reason` is the envelope's `details.reason`, when present.
    static func appError(status: Int, message: String?, reason: String?) -> AppError {
        switch status {
        case 401: .unauthenticated
        case 404: .notFound
        case 409: .conflict(reason: reason)
        case 422: .validation(message: message ?? "")
        case 429: .rateLimited
        case 503: .serviceUnavailable
        default: .server(status: status)
        }
    }

    static func appError(status: Int, envelope: Components.Schemas.ErrorEnvelope?) -> AppError {
        let details = envelope?.error.details?.value as? [String: (any Sendable)?]
        let reason = details?["reason"] as? String
        return appError(status: status, message: envelope?.error.message, reason: reason)
    }

    /// A thrown error. Cancellation is not a failure: it is rethrown as `CancellationError`.
    static func appError(from error: any Error) throws -> AppError {
        if let appError = error as? AppError {
            return appError
        }
        if error is CancellationError {
            throw CancellationError()
        }
        if let clientError = error as? ClientError {
            return try appError(from: clientError.underlyingError)
        }
        guard let urlError = error as? URLError else {
            return .server(status: 0)
        }
        switch urlError.code {
        case .cancelled:
            throw CancellationError()
        case .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed:
            return .offline
        case .timedOut:
            return .timedOut
        default:
            return .server(status: 0)
        }
    }
}
