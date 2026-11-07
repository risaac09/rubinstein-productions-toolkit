// vision_person: a person matte per image from macOS Vision's person segmentation.
// Built and cached by rpresolve.cutout (swiftc -O); macOS 12 or later.
//
// Usage: vision_person [--quality accurate|balanced|fast] OUT_DIR IMAGE...
// Writes OUT_DIR/<image basename> as an 8-bit grey PNG the size of the image
// (255 = person) and prints one JSON object per image, one per line:
//   {"path": "...", "matte": "...", "coverage": 0.31}
// coverage is the mean matte value over the frame, 0 to 1. One request object
// serves every frame, so Vision keeps the matte steady from frame to frame.

import Foundation
import ImageIO
import Vision

func jsonString(_ object: Any) -> String {
    let data = try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    return String(data: data, encoding: .utf8)!
}

var args = Array(CommandLine.arguments.dropFirst())
let request = VNGeneratePersonSegmentationRequest()
request.qualityLevel = .accurate
if args.first == "--quality" {
    args.removeFirst()
    switch args.isEmpty ? "" : args.removeFirst() {
    case "accurate": request.qualityLevel = .accurate
    case "balanced": request.qualityLevel = .balanced
    case "fast": request.qualityLevel = .fast
    default:
        FileHandle.standardError.write("vision_person: --quality takes accurate, balanced or fast\n".data(using: .utf8)!)
        exit(2)
    }
}
request.outputPixelFormat = kCVPixelFormatType_OneComponent8
guard !args.isEmpty else {
    FileHandle.standardError.write("usage: vision_person [--quality Q] OUT_DIR IMAGE...\n".data(using: .utf8)!)
    exit(2)
}
let outDir = args.removeFirst()
let gray = CGColorSpaceCreateDeviceGray()

for path in args {
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let cg = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        print(jsonString(["path": path, "error": "unreadable image"]))
        continue
    }
    do {
        try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
    } catch {
        print(jsonString(["path": path, "error": "\(error)"]))
        continue
    }
    guard let observation = request.results?.first else {
        print(jsonString(["path": path, "error": "no segmentation result"]))
        continue
    }
    let pb = observation.pixelBuffer
    CVPixelBufferLockBaseAddress(pb, .readOnly)
    let w = CVPixelBufferGetWidth(pb), h = CVPixelBufferGetHeight(pb), bpr = CVPixelBufferGetBytesPerRow(pb)
    let base = CVPixelBufferGetBaseAddress(pb)!.assumingMemoryBound(to: UInt8.self)
    var sum = 0
    for y in 0..<h { for x in 0..<w { sum += Int(base[y * bpr + x]) } }
    let data = Data(bytes: base, count: bpr * h)
    CVPixelBufferUnlockBaseAddress(pb, .readOnly)
    let coverage = Double(sum) / Double(max(w * h, 1) * 255)
    // The matte comes back smaller than the frame at balanced and fast; draw it at frame size.
    guard let provider = CGDataProvider(data: data as CFData),
          let matte = CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: bpr,
                              space: gray, bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                              provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent),
          let ctx = CGContext(data: nil, width: cg.width, height: cg.height, bitsPerComponent: 8, bytesPerRow: 0,
                              space: gray, bitmapInfo: CGImageAlphaInfo.none.rawValue) else {
        print(jsonString(["path": path, "error": "cannot build the matte image"]))
        continue
    }
    ctx.interpolationQuality = .high
    ctx.draw(matte, in: CGRect(x: 0, y: 0, width: cg.width, height: cg.height))
    let dest = (outDir as NSString).appendingPathComponent((path as NSString).lastPathComponent)
    guard let out = ctx.makeImage(),
          let sink = CGImageDestinationCreateWithURL(URL(fileURLWithPath: dest) as CFURL, "public.png" as CFString, 1, nil) else {
        print(jsonString(["path": path, "error": "cannot write \(dest)"]))
        continue
    }
    CGImageDestinationAddImage(sink, out, nil)
    guard CGImageDestinationFinalize(sink) else {
        print(jsonString(["path": path, "error": "cannot write \(dest)"]))
        continue
    }
    print(jsonString(["path": path, "matte": dest, "coverage": coverage]))
}
