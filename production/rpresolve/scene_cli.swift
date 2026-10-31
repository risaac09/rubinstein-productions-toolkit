// scene_cli: per-frame scene reads from macOS Vision, JSON on stdout.
// Built and cached by rpresolve.scene (swiftc -O); macOS only. No model download.
//
// Usage:
//   scene_cli saliency  IMAGE   -> attention and objectness boxes (top-left origin, normalized)
//   scene_cli sharpness IMAGE   -> variance of the Laplacian: salient region, whole frame, ratio
//   scene_cli motion    A B     -> mean absolute difference of two frames, 0..1 (not optical flow)
//   scene_cli depth     IMAGE [--depth-model PATH.mlmodelc]
//                               -> {"depth": null, "reason": "no model"} without a model path
// Exit 0 with JSON on success; a JSON {"error": ...} and exit 1 on failure.

import AppKit
import Foundation
import Vision

func emit(_ object: [String: Any], code: Int32 = 0) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
    exit(code)
}

func load(_ path: String) -> CGImage {
    guard let image = NSImage(contentsOfFile: path),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        emit(["error": "unreadable image", "path": path], code: 1)
    }
    return cg
}

// 8-bit gray pixels at the requested size (nil = native).
func gray(_ cg: CGImage, width: Int? = nil, height: Int? = nil) -> (px: [UInt8], w: Int, h: Int) {
    let w = width ?? cg.width, h = height ?? cg.height
    var px = [UInt8](repeating: 0, count: w * h)
    px.withUnsafeMutableBytes { buf in
        let ctx = CGContext(data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                            space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)!
        ctx.interpolationQuality = .medium
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    }
    return (px, w, h)
}

struct Box { var x: Double, y: Double, w: Double, h: Double }

func dict(_ b: Box) -> [String: Any] { ["x": b.x, "y": b.y, "w": b.w, "h": b.h] }

// Salient objects Vision returns, flipped to a top-left origin.
func salient(_ request: VNImageBasedRequest, _ cg: CGImage) -> [Box] {
    do { try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request]) }
    catch { emit(["error": "\(error)"], code: 1) }
    let obs = (request.results as? [VNSaliencyImageObservation])?.first
    return (obs?.salientObjects ?? []).map {
        Box(x: Double($0.boundingBox.minX), y: Double(1 - $0.boundingBox.maxY),
            w: Double($0.boundingBox.width), h: Double($0.boundingBox.height))
    }
}

func union(_ boxes: [Box]) -> Box? {
    guard let f = boxes.first else { return nil }
    var x0 = f.x, y0 = f.y, x1 = f.x + f.w, y1 = f.y + f.h
    for b in boxes.dropFirst() { x0 = min(x0, b.x); y0 = min(y0, b.y); x1 = max(x1, b.x + b.w); y1 = max(y1, b.y + b.h) }
    return Box(x: x0, y: y0, w: x1 - x0, h: y1 - y0)
}

// Variance of the 4-neighbour Laplacian over a pixel rectangle (interior pixels only).
func lapVar(_ g: (px: [UInt8], w: Int, h: Int), x0: Int, y0: Int, x1: Int, y1: Int) -> Double {
    let xa = max(x0, 1), xb = min(x1, g.w - 1), ya = max(y0, 1), yb = min(y1, g.h - 1)
    guard xb > xa, yb > ya else { return 0 }
    var sum = 0.0, sq = 0.0, n = 0.0
    for y in ya..<yb {
        for x in xa..<xb {
            let c = Double(g.px[y * g.w + x])
            let l = Double(g.px[y * g.w + x - 1]) + Double(g.px[y * g.w + x + 1])
                  + Double(g.px[(y - 1) * g.w + x]) + Double(g.px[(y + 1) * g.w + x]) - 4 * c
            sum += l; sq += l * l; n += 1
        }
    }
    return sq / n - (sum / n) * (sum / n)
}

let args = Array(CommandLine.arguments.dropFirst())
guard let cmd = args.first else { emit(["error": "usage: scene_cli saliency|sharpness|motion|depth ..."], code: 1) }

switch cmd {
case "saliency":
    guard args.count == 2 else { emit(["error": "usage: scene_cli saliency IMAGE"], code: 1) }
    let cg = load(args[1])
    let att = salient(VNGenerateAttentionBasedSaliencyImageRequest(), cg)
    let obj = salient(VNGenerateObjectnessBasedSaliencyImageRequest(), cg)
    emit(["width": cg.width, "height": cg.height,
          "attention": att.map(dict), "objectness": obj.map(dict)])
case "sharpness":
    guard args.count == 2 else { emit(["error": "usage: scene_cli sharpness IMAGE"], code: 1) }
    let cg = load(args[1])
    let g = gray(cg)
    let whole = lapVar(g, x0: 0, y0: 0, x1: g.w, y1: g.h)
    var out: [String: Any] = ["frame": whole, "region": NSNull(), "box": NSNull(), "ratio": NSNull()]
    if let b = union(salient(VNGenerateAttentionBasedSaliencyImageRequest(), cg)) {
        let region = lapVar(g, x0: Int(b.x * Double(g.w)), y0: Int(b.y * Double(g.h)),
                            x1: Int((b.x + b.w) * Double(g.w)), y1: Int((b.y + b.h) * Double(g.h)))
        out["region"] = region
        out["box"] = dict(b)
        if whole > 0 { out["ratio"] = region / whole }
    }
    emit(out)
case "motion":
    guard args.count == 3 else { emit(["error": "usage: scene_cli motion A B"], code: 1) }
    let a = gray(load(args[1]), width: 160, height: 90), b = gray(load(args[2]), width: 160, height: 90)
    var d = 0.0
    for i in 0..<a.px.count { d += abs(Double(a.px[i]) - Double(b.px[i])) }
    emit(["mad": d / Double(a.px.count) / 255.0, "method": "mean absolute difference at 160x90"])
case "depth":
    guard args.count >= 2 else { emit(["error": "usage: scene_cli depth IMAGE [--depth-model PATH]"], code: 1) }
    guard let i = args.firstIndex(of: "--depth-model"), i + 1 < args.count else {
        emit(["depth": NSNull(), "reason": "no model"])
    }
    let path = args[i + 1]
    guard FileManager.default.fileExists(atPath: path) else {
        emit(["depth": NSNull(), "reason": "model path not found", "path": path])
    }
    // Scaffold: the model is not wired in. Running it waits on the maintainer's
    // approval of the checkpoint download.
    emit(["depth": NSNull(), "reason": "model run not implemented", "path": path])
default:
    emit(["error": "unknown subcommand \(cmd)"], code: 1)
}
