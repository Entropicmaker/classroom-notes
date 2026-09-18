import Foundation
import Vision

struct TextBlock: Codable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct ImageResult: Codable {
    let source: String
    let languages: [String]
    let blocks: [TextBlock]
    let fullText: String
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard !arguments.isEmpty else {
    FileHandle.standardError.write(Data("usage: swift vision_ocr.swift IMAGE [IMAGE ...]\n".utf8))
    exit(2)
}

let languages = ["zh-Hans", "zh-Hant", "en-US"]
var results: [ImageResult] = []

for path in arguments {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = languages
    request.minimumTextHeight = 0.008

    let url = URL(fileURLWithPath: path)
    let handler = VNImageRequestHandler(url: url, options: [:])
    do {
        try handler.perform([request])
    } catch {
        FileHandle.standardError.write(Data("vision_ocr: \(path): \(error)\n".utf8))
        exit(1)
    }

    let blocks = (request.results ?? []).compactMap { observation -> TextBlock? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return TextBlock(
            text: candidate.string,
            confidence: candidate.confidence,
            x: box.origin.x,
            y: box.origin.y,
            width: box.size.width,
            height: box.size.height
        )
    }
    results.append(ImageResult(
        source: url.standardizedFileURL.path,
        languages: languages,
        blocks: blocks,
        fullText: blocks.map(\.text).joined(separator: "\n")
    ))
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
do {
    FileHandle.standardOutput.write(try encoder.encode(results))
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    FileHandle.standardError.write(Data("vision_ocr: JSON encoding failed: \(error)\n".utf8))
    exit(1)
}
