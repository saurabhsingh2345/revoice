// screcord <out.mov> <x> <y> <w> <h>   (points; records that region of the main display, cursor ON)
// Prints "STARTED <unix time>" when the first frame is written (exact marker sync) and stops
// cleanly on SIGINT / SIGTERM, finalising the file. AVCaptureScreenInput (ffmpeg avfoundation)
// no longer draws the cursor on macOS 26, which is why this exists.
import AVFoundation
import AppKit
import Foundation
import ScreenCaptureKit

setvbuf(stdout, nil, _IOLBF, 0)
let a = CommandLine.arguments
guard a.count == 6, let x = Double(a[2]), let y = Double(a[3]), let w = Double(a[4]), let h = Double(a[5]) else {
    print("usage: screcord out.mov x y w h"); exit(1)
}

final class Rec: NSObject, SCStreamDelegate, SCRecordingOutputDelegate {
    let rect: CGRect
    let path: String
    init(rect: CGRect, path: String) { self.rect = rect; self.path = path }
    var stream: SCStream?
    var output: SCRecordingOutput?

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() }) ?? content.displays.first else {
            print("FAIL no display"); exit(2)
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let cfg = SCStreamConfiguration()
        cfg.sourceRect = rect
        cfg.width = Int(rect.width * 2)
        cfg.height = Int(rect.height * 2)
        cfg.showsCursor = true
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 30)
        cfg.queueDepth = 8
        let rc = SCRecordingOutputConfiguration()
        rc.outputURL = URL(fileURLWithPath: path)
        rc.outputFileType = .mov
        rc.videoCodecType = .h264
        let ro = SCRecordingOutput(configuration: rc, delegate: self)
        let s = SCStream(filter: filter, configuration: cfg, delegate: self)
        try s.addRecordingOutput(ro)
        stream = s; output = ro
        try await s.startCapture()
    }

    func stop() {
        Task {
            do { try await stream?.stopCapture(); print("STOPCAPTURE ok") } catch { print("STOPCAPTURE err \(error)") }
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            print("FINISHED (timeout path)"); exit(0)
        }
    }

    func recordingOutputDidStartRecording(_ r: SCRecordingOutput) {
        print(String(format: "STARTED %.3f", Date().timeIntervalSince1970))
    }
    func recordingOutputDidFinishRecording(_ r: SCRecordingOutput) { print("FINISHED"); exit(0) }
    func recordingOutput(_ r: SCRecordingOutput, didFailWithError e: Error) { print("FAIL \(e)"); exit(2) }
    func stream(_ s: SCStream, didStopWithError e: Error) { print("STREAM STOPPED \(e)") }
}

let rec = Rec(rect: CGRect(x: x, y: y, width: w, height: h), path: a[1])
signal(SIGINT, SIG_IGN); signal(SIGTERM, SIG_IGN)
var sources: [DispatchSourceSignal] = []
for sig in [SIGINT, SIGTERM] {
    let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    src.setEventHandler { rec.stop() }
    src.resume(); sources.append(src)
}
Task {
    do { try await rec.start() } catch { print("FAIL \(error)"); exit(2) }
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
app.run()
