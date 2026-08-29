import Foundation
import Vision

private struct VisionProfile: Codable {
    let engine: String
    let recognitionLanguages: [String]
    let recognitionLevel: String
    let usesLanguageCorrection: Bool
    let minimumTextHeight: Float
    let readingOrder: String
}

private struct WorkerJob: Codable {
    let scanPage: Int
    let renderFile: String
    let jsonOutput: String
    let textOutput: String
    let profile: VisionProfile
}

private struct OCRLine: Codable {
    let text: String
    let confidence: Float
    let boundingBox: [Double]
}

// Keep the established fields so existing page readers continue to work.  The
// Python coordinator validates these bytes and adds the schema-v2 provenance.
private struct OCRPage: Codable {
    let scanPage: Int
    let renderFile: String
    let engine: String
    let recognitionLanguages: [String]
    let recognitionLevel: String
    let usesLanguageCorrection: Bool
    let generatedAt: String
    let lines: [OCRLine]
}

private struct WorkerResponse: Codable {
    let scanPage: Int
    let status: String
    let lineCount: Int?
    let error: String?
}

private enum WorkerError: Error, CustomStringConvertible {
    case invalidProfile(String)
    case missingRender(String)
    case vision(String)
    case write(String)

    var description: String {
        switch self {
        case .invalidProfile(let message): return message
        case .missingRender(let path): return "missing render: \(path)"
        case .vision(let message): return "Vision failed: \(message)"
        case .write(let message): return "staging write failed: \(message)"
        }
    }
}

private let decoder = JSONDecoder()
private let encoder: JSONEncoder = {
    let value = JSONEncoder()
    value.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    return value
}()
private let responseEncoder: JSONEncoder = {
    let value = JSONEncoder()
    value.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    return value
}()

private func emit(_ response: WorkerResponse) {
    do {
        let data = try responseEncoder.encode(response)
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    } catch {
        FileHandle.standardError.write(Data("response encoding failed: \(error)\n".utf8))
        exit(2)
    }
}

private func recognitionLevel(_ name: String) throws -> VNRequestTextRecognitionLevel {
    switch name {
    case "accurate": return .accurate
    case "fast": return .fast
    default: throw WorkerError.invalidProfile("invalid recognition level: \(name)")
    }
}

private func process(_ job: WorkerJob) throws -> Int {
    guard job.scanPage > 0 else {
        throw WorkerError.invalidProfile("scanPage must be positive")
    }
    guard job.profile.engine == "Apple Vision VNRecognizeTextRequest" else {
        throw WorkerError.invalidProfile("unexpected OCR engine")
    }
    guard job.profile.readingOrder == "top-to-bottom-then-left-to-right" else {
        throw WorkerError.invalidProfile("unsupported reading order")
    }

    let renderURL = URL(fileURLWithPath: job.renderFile)
    guard FileManager.default.fileExists(atPath: renderURL.path) else {
        throw WorkerError.missingRender(renderURL.path)
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = try recognitionLevel(job.profile.recognitionLevel)
    request.recognitionLanguages = job.profile.recognitionLanguages
    request.usesLanguageCorrection = job.profile.usesLanguageCorrection
    request.minimumTextHeight = job.profile.minimumTextHeight

    let handler = VNImageRequestHandler(url: renderURL, options: [:])
    do {
        try handler.perform([request])
    } catch {
        throw WorkerError.vision(String(describing: error))
    }

    let observations = request.results ?? []
    let ordered = observations.sorted { left, right in
        let leftTop = left.boundingBox.origin.y + left.boundingBox.height
        let rightTop = right.boundingBox.origin.y + right.boundingBox.height
        let rowTolerance = max(left.boundingBox.height, right.boundingBox.height) * 0.55
        if abs(leftTop - rightTop) > rowTolerance {
            return leftTop > rightTop
        }
        return left.boundingBox.origin.x < right.boundingBox.origin.x
    }

    let lines: [OCRLine] = ordered.compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return OCRLine(
            text: candidate.string,
            confidence: candidate.confidence,
            boundingBox: [box.origin.x, box.origin.y, box.width, box.height]
        )
    }

    let page = OCRPage(
        scanPage: job.scanPage,
        renderFile: renderURL.path,
        engine: job.profile.engine,
        recognitionLanguages: job.profile.recognitionLanguages,
        recognitionLevel: job.profile.recognitionLevel,
        usesLanguageCorrection: job.profile.usesLanguageCorrection,
        generatedAt: ISO8601DateFormatter().string(from: Date()),
        lines: lines
    )

    do {
        let jsonURL = URL(fileURLWithPath: job.jsonOutput)
        let textURL = URL(fileURLWithPath: job.textOutput)
        try FileManager.default.createDirectory(
            at: jsonURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        var jsonData = try encoder.encode(page)
        jsonData.append(Data("\n".utf8))
        try jsonData.write(to: jsonURL, options: .atomic)
        let text = lines.map(\.text).joined(separator: "\n") + "\n"
        try Data(text.utf8).write(to: textURL, options: .atomic)
    } catch {
        throw WorkerError.write(String(describing: error))
    }
    return lines.count
}

guard CommandLine.arguments.count == 2, CommandLine.arguments[1] == "--worker" else {
    FileHandle.standardError.write(
        Data("usage: apple_vision_ocr --worker\n".utf8)
    )
    exit(2)
}

while let input = readLine(strippingNewline: true) {
    if input.isEmpty { continue }
    var scanPage = 0
    do {
        let job = try decoder.decode(WorkerJob.self, from: Data(input.utf8))
        scanPage = job.scanPage
        let count = try autoreleasepool { try process(job) }
        emit(WorkerResponse(scanPage: scanPage, status: "ok", lineCount: count, error: nil))
    } catch {
        emit(
            WorkerResponse(
                scanPage: scanPage,
                status: "error",
                lineCount: nil,
                error: String(describing: error)
            )
        )
    }
}
