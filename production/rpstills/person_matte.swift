// person_matte: the person under a point, as a grey PNG matte, from macOS Vision.
//
//   person_matte IN.png OUT.png X Y [IN OUT X Y ...]
//
// X Y are normalized from the top left (the convention of vision_stills). The foreground instance Vision finds at
// that point is written as an 8-bit matte the size of IN (255 = the person); a point on no instance takes every
// instance. Prints one JSON line per job: {"path", "ok", "instances"} or {"path", "error"}.
import Foundation
import Vision
import CoreImage
import CoreVideo
import AppKit

func jsonString(_ d: [String: Any]) -> String {
    guard let data = try? JSONSerialization.data(withJSONObject: d, options: [.sortedKeys]),
          let s = String(data: data, encoding: .utf8) else { return "{}" }
    return s
}

let args = Array(CommandLine.arguments.dropFirst())
let ctx = CIContext()
var i = 0
while i + 3 < args.count {
    let path = args[i], out = args[i + 1]
    let px = Double(args[i + 2]) ?? 0.5, py = Double(args[i + 3]) ?? 0.5
    i += 4
    guard let image = NSImage(contentsOfFile: path),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print(jsonString(["path": path, "error": "unreadable image"])); continue
    }
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    let req = VNGenerateForegroundInstanceMaskRequest()
    do {
        try handler.perform([req])
        guard let obs = req.results?.first else { print(jsonString(["path": path, "error": "no person found"])); continue }
        var pick = obs.allInstances
        let map = obs.instanceMask
        CVPixelBufferLockBaseAddress(map, .readOnly)
        let w = CVPixelBufferGetWidth(map), h = CVPixelBufferGetHeight(map)
        if let base = CVPixelBufferGetBaseAddress(map) {
            let x = min(w - 1, max(0, Int(px * Double(w)))), y = min(h - 1, max(0, Int(py * Double(h))))
            let label = Int(base.assumingMemoryBound(to: UInt8.self)[y * CVPixelBufferGetBytesPerRow(map) + x])
            if label > 0 { pick = IndexSet(integer: label) }
        }
        CVPixelBufferUnlockBaseAddress(map, .readOnly)
        let mask = try obs.generateScaledMaskForImage(forInstances: pick, from: handler)
        try ctx.writePNGRepresentation(of: CIImage(cvPixelBuffer: mask), to: URL(fileURLWithPath: out),
                                       format: .L8, colorSpace: CGColorSpaceCreateDeviceGray())
        print(jsonString(["path": path, "ok": true, "instances": obs.allInstances.count]))
    } catch {
        print(jsonString(["path": path, "error": "\(error)"]))
    }
}
