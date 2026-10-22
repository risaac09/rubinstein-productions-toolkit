// develop_raw: one RAW file to an sRGB TIFF through Apple's RAW engine (CIRAWFilter).
//
//   develop_raw --info RAW
//       one JSON line: {"width", "height", "temp", "tint", "orientation"}, the
//       native size and the engine's as-shot white balance (kelvin, tint)
//   develop_raw RAW OUT [--exposure EV] [--temp K] [--tint T] [--long-edge N] [--depth 8|16]
//       develops RAW with those settings (unset ones keep the engine's
//       defaults) and writes OUT as a TIFF with the sRGB profile, 16-bit
//       unless --depth 8; --long-edge scales the long side down to N px (never
//       up). Prints one JSON line {"ok": true, "width", "height"} or
//       {"error": "..."} and exits non-zero on error.
//
// rpstills.develop compiles this once into ~/Library/Caches/rpstills, as
// rpstills.vision does for its helper.
import CoreImage
import Foundation

func emit(_ doc: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: doc, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}

func fail(_ message: String) -> Never {
    emit(["error": message])
    exit(1)
}

var info = false
var opts: [String: String] = [:]
var paths: [String] = []
var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let a = args.removeFirst()
    if a == "--info" {
        info = true
    } else if a.hasPrefix("--") {
        guard !args.isEmpty else { fail("\(a) needs a value") }
        opts[String(a.dropFirst(2))] = args.removeFirst()
    } else {
        paths.append(a)
    }
}

func number(_ key: String) -> Float? {
    guard let v = opts[key] else { return nil }
    guard let f = Float(v), f.isFinite else { fail("--\(key) is not a number: \(v)") }
    return f
}

guard paths.count == (info ? 1 : 2) else {
    fail("usage: develop_raw --info RAW | develop_raw RAW OUT [--exposure EV] [--temp K] [--tint T] [--long-edge N] [--depth 8|16]")
}
// CIRAWFilter hands back a filter of size zero for a missing or unsupported file
guard let filter = CIRAWFilter(imageURL: URL(fileURLWithPath: paths[0])), filter.nativeSize.width > 0 else {
    fail("CIRAWFilter cannot open \(paths[0])")
}
let native = filter.nativeSize
if info {
    emit(["width": Int(native.width), "height": Int(native.height), "temp": filter.neutralTemperature,
          "tint": filter.neutralTint, "orientation": Int(filter.orientation.rawValue)])
    exit(0)
}
if let v = number("exposure") { filter.exposure = v }
if let v = number("temp") { filter.neutralTemperature = v }
if let v = number("tint") { filter.neutralTint = v }
if let edge = number("long-edge"), edge > 0 {
    let longest = Float(max(native.width, native.height))
    if edge < longest { filter.scaleFactor = edge / longest }
}
let depth = opts["depth"] ?? "16"
guard depth == "8" || depth == "16" else { fail("--depth is 8 or 16, not \(depth)") }
guard let image = filter.outputImage else { fail("CIRAWFilter returned no image for \(paths[0])") }

let srgb = CGColorSpace(name: CGColorSpace.sRGB)!
let context = CIContext(options: [.workingColorSpace: CGColorSpace(name: CGColorSpace.extendedLinearSRGB)!])
do {
    try context.writeTIFFRepresentation(of: image, to: URL(fileURLWithPath: paths[1]),
                                        format: depth == "8" ? .RGBA8 : .RGBA16, colorSpace: srgb)
} catch {
    fail("cannot write \(paths[1]): \(error.localizedDescription)")
}
emit(["ok": true, "width": Int(image.extent.width.rounded()), "height": Int(image.extent.height.rounded())])
