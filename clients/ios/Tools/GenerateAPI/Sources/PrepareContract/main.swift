import ContractRewrite
import Foundation

// prepare-contract <input openapi.json> <output openapi.json>
// Writes the generator's copy of the contract (see ContractRewrite). Used only by
// clients/ios/scripts/generate-api.sh; clients/openapi.json itself is never changed.
let arguments = CommandLine.arguments
guard arguments.count == 3 else {
    FileHandle.standardError.write(Data("usage: prepare-contract <input> <output>\n".utf8))
          exit(2)
}
do {
    let input = try Data(contentsOf: URL(fileURLWithPath: arguments[1]))
    try ContractRewrite.rewrite(input).write(to: URL(fileURLWithPath: arguments[2]))
} catch {
    FileHandle.standardError.write(Data("prepare-contract: \(error)\n".utf8))
    exit(1)
}
