// vision_stills: faces with capture quality and eye state from macOS Vision,
// for ranking frames of a stills shoot. Built and cached by rpstills.vision.
//
// Usage: vision_stills [--landmarks] IMAGE...
// Output: one JSON object per image, one per line:
//   {"path": "...", "width": W, "height": H, "faces": [
//      {"x": 0.41, "y": 0.18, "w": 0.21, "h": 0.30, "confidence": 0.87,
//       "quality": 0.62, "eye_left": 0.31, "eye_right": 0.29,
//       "roll": 0.02, "yaw": -0.1}],
//    "people": [{"x": 0.30, "y": 0.10, "w": 0.25, "h": 0.80, "confidence": 0.9}]}
// people are whole-body boxes (VNDetectHumanRectanglesRequest), for frames
// where no face is large enough to find: a performer seen from a distance, or
// from behind. Same normalized, top-left convention as faces.
// Boxes are normalized with the origin at the TOP left. quality is Vision's
// face capture quality (0 to 1, comparable only between faces of the same
// person). eye_* is the eye aspect ratio (height over width of the eye
// landmark polygon); roughly 0.25 and up is open, under 0.15 is closed.
// NSImage applies the file's EXIF orientation: sizes and points are of the
// upright image, which differs from the stored pixels when the tag is not 1.
// --landmarks (opt-in, first argument) adds to each face
//   "landmarks": {"leftEye": [[x, y], ...], "rightEye", "leftEyebrow",
//                 "rightEyebrow", "outerLips", "innerLips", "faceContour", "nose"}
// as points in the same normalized, top-left, whole-image convention as the
// box. Vision's left and right are its own naming; callers pair by image x.
// Without the flag the output is unchanged.
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

// Landmark points are normalized inside the face box with a bottom-left origin.
func points(_ region: VNFaceLandmarkRegion2D?, _ b: CGRect) -> [[Double]]? {
    guard let r = region, r.pointCount > 0 else { return nil }
    return r.normalizedPoints.map { p in
        let x = Double(b.minX) + Double(p.x) * Double(b.width)
        let y = 1 - (Double(b.minY) + Double(p.y) * Double(b.height))
        return [(x * 1e6).rounded() / 1e6, (y * 1e6).rounded() / 1e6]
    }
}

var paths = Array(CommandLine.arguments.dropFirst())
let wantLandmarks = paths.first == "--landmarks"
if wantLandmarks { paths.removeFirst() }

for path in paths {
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
    let humans = VNDetectHumanRectanglesRequest()
    humans.upperBodyOnly = false
    var people: [[String: Any]] = []
    if (try? handler.perform([humans])) != nil {
        for p in humans.results ?? [] {
            let b = p.boundingBox
            people.append(["x": Double(b.minX), "y": Double(1 - b.maxY), "w": Double(b.width),
                           "h": Double(b.height), "confidence": Double(p.confidence)])
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
        if wantLandmarks, let l = face.landmarks {
            var marks: [String: Any] = [:]
            let regions: [(String, VNFaceLandmarkRegion2D?)] = [
                ("leftEye", l.leftEye), ("rightEye", l.rightEye), ("leftEyebrow", l.leftEyebrow),
                ("rightEyebrow", l.rightEyebrow), ("outerLips", l.outerLips), ("innerLips", l.innerLips),
                ("faceContour", l.faceContour), ("nose", l.nose)]
            for (name, region) in regions {
                if let pts = points(region, b) { marks[name] = pts }
            }
            d["landmarks"] = marks
        }
        faces.append(d)
    }
    print(jsonString(["path": path, "width": cg.width, "height": cg.height, "faces": faces,
                      "people": people]))
}
