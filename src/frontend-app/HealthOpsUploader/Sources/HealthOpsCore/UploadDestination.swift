import Foundation

/// A physical phone must never send HealthKit samples to an undeclared or synthetic server.
/// This guards accidental environment mixing; the API independently checks the upload header.
public enum UploadDestination {
    public static func rejection(server: URL, mode: String?, simulator: Bool) -> String? {
        guard server.user == nil, server.password == nil, server.query == nil,
              server.fragment == nil, server.host != nil else {
            return "Use a server URL without credentials, query parameters or fragments."
        }
        if simulator {
            return ["development", "synthetic"].contains(mode ?? "")
                ? nil : "Simulator samples require a development or synthetic environment."
        }
        guard server.scheme == "https" else { return "Personal uploads require HTTPS." }
        return mode == "personal" ? nil
            : "This server is not a personal-data environment. HealthKit upload is blocked."
    }

    public static func dataKind(simulator: Bool) -> String {
        simulator ? "synthetic" : "personal"
    }
}
