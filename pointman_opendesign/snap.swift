// snap <file.html> <out.png> <width> <height> [wait seconds]: a WebKit still of a page, scripts run (loupe, thread 649).
import AppKit
import WebKit

let a = CommandLine.arguments
let (file, out) = (URL(fileURLWithPath: a[1]), a[2])
let size = CGSize(width: Double(a[3])!, height: Double(a[4])!)
let wait = a.count > 5 ? Double(a[5])! : 3

final class Snap: NSObject, WKNavigationDelegate {
    let web: WKWebView
    init(_ size: CGSize) {
        web = WKWebView(frame: CGRect(origin: .zero, size: size))
        super.init()
        web.navigationDelegate = self
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + wait) {
            webView.evaluateJavaScript("document.documentElement.scrollHeight") { h, _ in
                let height = max(size.height, (h as? Double) ?? size.height)
                webView.frame.size.height = height
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                    let config = WKSnapshotConfiguration()
                    config.rect = CGRect(x: 0, y: 0, width: size.width, height: height)
                    webView.takeSnapshot(with: config) { image, error in
                        guard let image, let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                              let png = rep.representation(using: .png, properties: [:]) else {
                            print("failed: \(String(describing: error))"); exit(1)
                        }
                        try! png.write(to: URL(fileURLWithPath: out))
                        print("wrote \(out) \(rep.pixelsWide)x\(rep.pixelsHigh)")
                        exit(0)
                    }
                }
            }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let window = NSWindow(contentRect: CGRect(x: -10000, y: 0, width: size.width, height: size.height), styleMask: [.borderless], backing: .buffered, defer: false)
let snap = Snap(size)
window.contentView = snap.web
window.orderBack(nil)
snap.web.loadFileURL(file, allowingReadAccessTo: file.deletingLastPathComponent())
DispatchQueue.main.asyncAfter(deadline: .now() + 60) { print("timed out"); exit(2) }
app.run()
