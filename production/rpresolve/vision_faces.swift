// vision_faces: print the face rectangles macOS Vision finds in each image.
// Built and cached by rpresolve.measure (swiftc -O); macOS only.
//
// Usage: vision_faces IMAGE...
// Output: one JSON object per image, one per line:
//   {"path": "...", "width": W, "height": H,
//    "faces": [{"x": 0.41, "y": 0.18, "w": 0.21, "h": 0.30, "confidence": 0.87}]}
// Boxes are normalized with the origin at the TOP left (Vision's own origin
// is bottom left; it is flipped here so callers index image rows directly).

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
    let request = VNDetectFaceRectanglesRequest()
    do {
        try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
    } catch {
        print(jsonString(["path": path, "error": "\(error)"]))
        continue
    }
    let faces: [[String: Any]] = (request.results ?? []).map { face in
        let b = face.boundingBox
        return ["x": Double(b.minX), "y": Double(1 - b.maxY), "w": Double(b.width),
                "h": Double(b.height), "confidence": Double(face.confidence)]
    }
    print(jsonString(["path": path, "width": cg.width, "height": cg.height, "faces": faces]))
}
