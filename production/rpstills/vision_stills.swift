// vision_stills: faces with capture quality and eye state from macOS Vision,
// for ranking frames of a stills shoot. Built and cached by rpstills.vision.
//
// Usage: vision_stills IMAGE...
// Output: one JSON object per image, one per line:
//   {"path": "...", "width": W, "height": H, "faces": [
//      {"x": 0.41, "y": 0.18, "w": 0.21, "h": 0.30, "confidence": 0.87,
//       "quality": 0.62, "eye_left": 0.31, "eye_right": 0.29,
//       "roll": 0.02, "yaw": -0.1}]}
// Boxes are normalized with the origin at the TOP left. quality is Vision's
// face capture quality (0 to 1, comparable only between faces of the same
// person). eye_* is the eye aspect ratio (height over width of the eye
// landmark polygon); roughly 0.25 and up is open, under 0.15 is closed.
// The image is taken as stored; pass upright files.
import AppKit
import Foundation
import Vision

func jsonString(_ object: Any) -> String {
    let data = try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    return String(data: data, encoding: .utf8)!
}

func aspect(_ region: VNFaceLandmarkRegion2D?) -> Double? {
    guard let r = region, r.pointCount >= 4 else { return nil }
    var minX = Double.infinity, maxX = -Double.infinity
    var minY = Double.infinity, maxY = -Double.infinity
    for p in r.normalizedPoints {
        minX = min(minX, Double(p.x)); maxX = max(maxX, Double(p.x))
        minY = min(minY, Double(p.y)); maxY = max(maxY, Double(p.y))
    }
    let w = maxX - minX
    return w > 0 ? (maxY - minY) / w : nil
}

for path in CommandLine.arguments.dropFirst() {
    guard let image = NSImage(contentsOfFile: path),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print(jsonString(["path": path, "error": "unreadable image"]))
        continue
    }
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    let landmarks = VNDetectFaceLandmarksRequest()
    do { try handler.perform([landmarks]) } catch {
        print(jsonString(["path": path, "error": "\(error)"]))
        continue
    }
    let found = landmarks.results ?? []
    var qualities: [Double?] = Array(repeating: nil, count: found.count)
    if !found.isEmpty {
        let quality = VNDetectFaceCaptureQualityRequest()
        quality.inputFaceObservations = found
        if (try? handler.perform([quality])) != nil, let qr = quality.results {
            for (i, obs) in qr.enumerated() where i < qualities.count {
                if let q = obs.faceCaptureQuality { qualities[i] = Double(q) }
            }
        }
    }
    var faces: [[String: Any]] = []
    for (i, face) in found.enumerated() {
        let b = face.boundingBox
        var d: [String: Any] = ["x": Double(b.minX), "y": Double(1 - b.maxY), "w": Double(b.width),
                                "h": Double(b.height), "confidence": Double(face.confidence)]
        if let q = qualities[i] { d["quality"] = q }
        if let l = aspect(face.landmarks?.leftEye) { d["eye_left"] = l }
        if let r = aspect(face.landmarks?.rightEye) { d["eye_right"] = r }
        if let roll = face.roll { d["roll"] = roll.doubleValue }
        if let yaw = face.yaw { d["yaw"] = yaw.doubleValue }
        faces.append(d)
    }
    print(jsonString(["path": path, "width": cg.width, "height": cg.height, "faces": faces]))
}
