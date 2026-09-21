// An original, code-drawn book icon; no external image asset or font required.
import AppKit

let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)

func render(_ size: Int, name: String) throws {
    let image = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                                bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: image)
    let scale = CGFloat(size) / 1024
    let transform = NSAffineTransform()
    transform.scale(by: scale)
    transform.concat()
    NSColor(calibratedRed: 0.13, green: 0.31, blue: 0.25, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 66, y: 66, width: 892, height: 892), xRadius: 196, yRadius: 196).fill()
    NSColor(calibratedRed: 0.97, green: 0.95, blue: 0.87, alpha: 1).setFill()
    let left = NSBezierPath()
    left.move(to: NSPoint(x: 204, y: 280))
    left.curve(to: NSPoint(x: 492, y: 234), controlPoint1: NSPoint(x: 321, y: 315), controlPoint2: NSPoint(x: 433, y: 279))
    left.line(to: NSPoint(x: 492, y: 714))
    left.curve(to: NSPoint(x: 204, y: 760), controlPoint1: NSPoint(x: 391, y: 778), controlPoint2: NSPoint(x: 299, y: 784))
    left.close()
    left.fill()
    let right = NSBezierPath()
    right.move(to: NSPoint(x: 532, y: 234))
    right.curve(to: NSPoint(x: 820, y: 280), controlPoint1: NSPoint(x: 611, y: 283), controlPoint2: NSPoint(x: 709, y: 308))
    right.line(to: NSPoint(x: 820, y: 760))
    right.curve(to: NSPoint(x: 532, y: 714), controlPoint1: NSPoint(x: 724, y: 787), controlPoint2: NSPoint(x: 625, y: 770))
    right.close()
    right.fill()
    NSColor(calibratedRed: 0.61, green: 0.70, blue: 0.60, alpha: 1).setStroke()
    for y in [420, 500, 580, 660] {
        let line = NSBezierPath()
        line.lineWidth = 14
        line.lineCapStyle = .round
        line.move(to: NSPoint(x: 259, y: y))
        line.curve(to: NSPoint(x: 432, y: y - 22), controlPoint1: NSPoint(x: 320, y: y + 5), controlPoint2: NSPoint(x: 375, y: y - 3))
        line.stroke()
    }
    NSColor(calibratedRed: 0.77, green: 0.46, blue: 0.28, alpha: 1).setFill()
    let bookmark = NSBezierPath()
    bookmark.move(to: NSPoint(x: 695, y: 772))
    bookmark.line(to: NSPoint(x: 752, y: 774))
    bookmark.line(to: NSPoint(x: 752, y: 566))
    bookmark.line(to: NSPoint(x: 723, y: 590))
    bookmark.line(to: NSPoint(x: 695, y: 566))
    bookmark.close()
    bookmark.fill()
    NSGraphicsContext.restoreGraphicsState()
    try image.representation(using: .png, properties: [:])!.write(to: destination.appendingPathComponent(name))
}

for logical in [16, 32, 128, 256, 512] {
    try render(logical, name: "icon_\(logical)x\(logical).png")
    try render(logical * 2, name: "icon_\(logical)x\(logical)@2x.png")
}
