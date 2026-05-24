import Foundation
import Darwin
import UserNotifications

func value(after flag: String, in args: [String]) -> String {
    guard let index = args.firstIndex(of: flag), index + 1 < args.count else {
        return ""
    }
    return args[index + 1]
}

func writeStatus(_ status: String) {
    let statusPath = value(after: "--status-file", in: args)
    guard !statusPath.isEmpty else {
        return
    }
    try? status.write(toFile: statusPath, atomically: true, encoding: .utf8)
}

let args = Array(CommandLine.arguments.dropFirst())
let titleValue = value(after: "--title", in: args)
let messageValue = value(after: "--message", in: args)
let soundValue = value(after: "--sound", in: args)

let content = UNMutableNotificationContent()
content.title = titleValue.isEmpty ? "Codex" : titleValue
content.body = messageValue.isEmpty ? content.title : messageValue
if !soundValue.isEmpty {
    content.sound = UNNotificationSound(named: UNNotificationSoundName(soundValue))
}

let center = UNUserNotificationCenter.current()
let authorizationSemaphore = DispatchSemaphore(value: 0)
var authorizationGranted = false
var authorizationError: Error?
center.requestAuthorization(options: [.alert, .sound]) { granted, error in
    authorizationGranted = granted
    authorizationError = error
    authorizationSemaphore.signal()
}
if authorizationSemaphore.wait(timeout: .now() + 2) == .timedOut {
    writeStatus("error: authorization timeout")
    exit(2)
}
if !authorizationGranted || authorizationError != nil {
    writeStatus("error: authorization denied")
    exit(2)
}

let request = UNNotificationRequest(
    identifier: UUID().uuidString,
    content: content,
    trigger: nil
)
let deliverySemaphore = DispatchSemaphore(value: 0)
var deliveryError: Error?
center.add(request) { error in
    deliveryError = error
    deliverySemaphore.signal()
}
if deliverySemaphore.wait(timeout: .now() + 2) == .timedOut {
    writeStatus("error: delivery timeout")
    exit(3)
}
if deliveryError != nil {
    writeStatus("error: delivery failed")
    exit(3)
}

writeStatus("ok")
