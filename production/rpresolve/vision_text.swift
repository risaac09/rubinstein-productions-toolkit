// vision_text: print the lines of text macOS Vision reads in each image.
// Built and cached by rpresolve.vision (swiftc -O), as vision_faces is; macOS only.
//
// Usage: vision_text IMAGE...
// Output: one JSON object per image, one per line:
//   {"path": "...", "width": W, "height": H,
//    "texts": [{"text": "HELLO", "x": 0.31, "y": 0.42, "w": 0.38, "h": 0.09, "confidence": 0.98}]}
// One entry per line Vision finds (its top candidate). Boxes are normalized with the origin
// at the TOP left, as vision_faces flips them.

import AppKit
import Foundation
import Vision

func jsonString(_ object: Any) -> String {
    let data = try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    return String(data: data, encoding: .utf8)!
}

for path in CommandLine.arguments.dropFirst() {
    guard let image = NSImage(contentsOfFile: path),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print(jsonString(["path": path, "error": "unreadable image"]))
        continue
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false  // a title is often a name or one word, not a sentence
    do {
        try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
    } catch {
        print(jsonString(["path": path, "error": "\(error)"]))
        continue
    }
    let texts: [[String: Any]] = (request.results ?? []).compactMap { obs in
        guard let top = obs.topCandidates(1).first else { return nil }
        let b = obs.boundingBox
        return ["text": top.string, "x": Double(b.minX), "y": Double(1 - b.maxY), "w": Double(b.width),
                "h": Double(b.height), "confidence": Double(top.confidence)]
    }
    print(jsonString(["path": path, "width": cg.width, "height": cg.height, "texts": texts]))
}
