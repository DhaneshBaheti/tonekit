#!/usr/bin/env python3
"""ToneKit — select text anywhere on macOS, rewrite its tone with Gemini."""

import os
import time
import threading
import logging
import objc
import requests
from Foundation import NSMakeRect, NSObject, NSPoint
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory,
    NSStatusBar, NSVariableStatusItemLength,
    NSMenu, NSMenuItem,
    NSPanel, NSWindow, NSBackingStoreBuffered,
    NSStackView, NSUserInterfaceLayoutOrientationVertical, NSLayoutAttributeLeading,
    NSButton, NSRoundedBezelStyle, NSControlSizeSmall,
    NSTextField, NSFont, NSFontWeightSemibold, NSColor,
    NSEvent, NSFloatingWindowLevel, NSWindowTitleHidden, NSEdgeInsets,
    NSPasteboard, NSPasteboardTypeString, NSWindowCloseButton,
    NSEventModifierFlagCommand, NSEventModifierFlagOption,
    NSEventMaskKeyDown, NSEventMaskLeftMouseDown,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskTitled, NSWindowStyleMaskFullSizeContentView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
)
from Quartz import (
    CGEventCreateKeyboardEvent, CGEventSetFlags, CGEventPost, CGEventSourceCreate,
    CGEventGetFlags, CGEventGetIntegerValueField,
    CGEventTapCreate, CGEventTapEnable, CGEventMaskBit,
    kCGHIDEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly,
    kCGEventKeyDown, kCGKeyboardEventKeycode,
    kCGEventFlagMaskCommand, kCGEventFlagMaskAlternate,
    kCGEventSourceStateHIDSystemState,
)
from CoreFoundation import (
    CFMachPortCreateRunLoopSource, CFRunLoopAddSource, CFRunLoopGetMain,
    kCFRunLoopCommonModes,
)
from ApplicationServices import (
    AXIsProcessTrusted, AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt,
    AXUIElementCreateSystemWide, AXUIElementCopyAttributeValue,
    kAXFocusedUIElementAttribute, kAXSelectedTextAttribute,
)
import PyObjCTools.AppHelper as AppHelper

logging.basicConfig(filename="/tmp/tonekit.log", level=logging.INFO,
                    format="%(asctime)s %(message)s")
log = logging.getLogger("tonekit")
hotkey_delegate = None


def hotkey_event_callback(proxy, event_type, event, refcon):
    log.info("event tap callback type=%s", event_type)
    if (event_type == kCGEventKeyDown
            and CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode) == KEY_E
            and CGEventGetFlags(event) & kCGEventFlagMaskCommand
            and CGEventGetFlags(event) & kCGEventFlagMaskAlternate):
        log.info("event tap matched hotkey")
        if hotkey_delegate is not None:
            AppHelper.callAfter(hotkey_delegate._handleHotKey)
    return event

# MARK: - Config

API_KEY      = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()

TONES = ["Formal", "Academic", "Professional", "Casual",
         "Fun", "Friendly", "Concise", "Persuasive"]

KEY_E, KEY_C, KEY_V, KEY_ESC = 14, 8, 9, 53   # virtual keycodes

# MARK: - Gemini API

def gemini_enhance(text, tone):
    if not API_KEY:
        raise RuntimeError("Set GEMINI_API_KEY before launching ToneKit")
    if not text or not text.strip():
        raise ValueError("Select some text first")

    prompt = (
        f"Rewrite the following text in a {tone.lower()} tone. "
        "Keep the meaning, key information, and approximate length. "
        "Match the original language. "
        "Output ONLY the rewritten text — no preamble, no quotes, no markdown fences.\n\n"
        f"Text:\n{text}"
    )
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7}}

    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY},
        json=body, timeout=30)

    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"Gemini API {r.status_code}: {msg}")

    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("Gemini returned an unexpected response") from exc
    out = "".join(p.get("text", "") for p in parts).strip().strip('"“”').strip()
    if not out:
        raise RuntimeError("Gemini returned empty text")
    return out

# MARK: - Clipboard + key simulation

def get_clipboard_text():
    return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)

def set_clipboard_text(s):
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(s, NSPasteboardTypeString)

def press_cmd_key(keycode):
    src = CGEventSourceCreate(kCGEventSourceStateHIDSystemState)
    down = CGEventCreateKeyboardEvent(src, keycode, True)
    up   = CGEventCreateKeyboardEvent(src, keycode, False)
    CGEventSetFlags(down, kCGEventFlagMaskCommand)
    CGEventSetFlags(up,   kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, down)
    time.sleep(0.03)
    CGEventPost(kCGHIDEventTap, up)
    time.sleep(0.03)

# MARK: - Text capture

def selected_text_via_ax():
    """Ask the focused UI element directly (no clipboard side effects)."""
    try:
        system_wide = AXUIElementCreateSystemWide()
        err, focused = AXUIElementCopyAttributeValue(system_wide, kAXFocusedUIElementAttribute, None)
        if err != 0 or focused is None:
            return None
        err, value = AXUIElementCopyAttributeValue(focused, kAXSelectedTextAttribute, None)
        if err != 0 or value is None:
            return None
        text = str(value)
        return text if text.strip() else None
    except Exception:
        return None

def selected_text_via_clipboard():
    """Fallback for apps that don't expose AX text: simulate ⌘C."""
    saved = get_clipboard_text()
    set_clipboard_text("")
    press_cmd_key(KEY_C)
    time.sleep(0.25)
    text = get_clipboard_text()
    if saved is not None:
        set_clipboard_text(saved)          # restore old clipboard
    return text if text else None

def capture_selected_text():
    return selected_text_via_ax() or selected_text_via_clipboard()

# MARK: - Banner

def show_banner(message):
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, 10, 10),
        NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
        NSBackingStoreBuffered, False)
    panel.setLevel_(NSFloatingWindowLevel)
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary)

    label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
    label.setStringValue_(message)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    box = NSStackView.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
    box.addArrangedSubview_(label)
    box.setEdgeInsets_(NSEdgeInsets(12, 16, 12, 16))
    box.setWantsLayer_(True)
    box.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
    box.layer().setCornerRadius_(10)
    panel.setContentView_(box)
    panel.setContentSize_(box.fittingSize())

    mouse = NSEvent.mouseLocation()
    panel.setFrameTopLeftPoint_(NSPoint(mouse.x - panel.frame().size.width / 2, mouse.y + 24))
    panel.orderFrontRegardless()
    threading.Timer(2.5, lambda: AppHelper.callAfter(panel.orderOut_, None)).start()

# MARK: - Tone popup (non-activating panel, so the target app keeps focus)

class TonePanel(NSPanel):
    def initWithText_(self, text):
        self = objc.super(TonePanel, self).initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 380, 140),
            NSWindowStyleMaskTitled | NSWindowStyleMaskFullSizeContentView
                | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered, False)
        if not self:
            return None
        self.originalText = text
        self.monitors = []
        self.onResult = None
        self.onDismiss = None

        self.setLevel_(NSFloatingWindowLevel)
        self.setMovableByWindowBackground_(True)
        self.setReleasedWhenClosed_(False)
        self.setHidesOnDeactivate_(False)
        self.setTitleVisibility_(NSWindowTitleHidden)
        self.setTitlebarAppearsTransparent_(True)
        self.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary)
        close = self.standardWindowButton_(NSWindowCloseButton)
        if close:
            close.setHidden_(True)
        self._buildContent()
        return self

    def _buildContent(self):
        root = NSStackView.alloc().init()
        root.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        root.setAlignment_(NSLayoutAttributeLeading)
        root.setSpacing_(10)
        root.setEdgeInsets_(NSEdgeInsets(14, 16, 14, 16))

        title = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
        title.setStringValue_("Rewrite in which tone?")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))

        row1 = NSStackView.alloc().init(); row1.setSpacing_(8)
        row2 = NSStackView.alloc().init(); row2.setSpacing_(8)
        for i, tone in enumerate(TONES):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
            b.setTitle_(tone)
            b.setTarget_(self)
            b.setAction_("toneChosen:")
            b.setBezelStyle_(NSRoundedBezelStyle)
            b.setControlSize_(NSControlSizeSmall)
            b.setTag_(i)
            (row1 if i < 4 else row2).addArrangedSubview_(b)

        self.status = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
        self.status.setStringValue_(f"{len(self.originalText)} chars · Esc to cancel")
        self.status.setBezeled_(False)
        self.status.setDrawsBackground_(False)
        self.status.setEditable_(False)
        self.status.setFont_(NSFont.systemFontOfSize_(11))
        self.status.setTextColor_(NSColor.secondaryLabelColor())

        root.addArrangedSubview_(title)
        root.addArrangedSubview_(row1)
        root.addArrangedSubview_(row2)
        root.addArrangedSubview_(self.status)
        self.setContentView_(root)
        self.setContentSize_(root.fittingSize())

    def present(self):
        log.info("presenting tone panel")
        mouse = NSEvent.mouseLocation()
        f = self.frame()
        self.setFrameTopLeftPoint_(NSPoint(mouse.x - f.size.width / 2, mouse.y + 24))
        self.makeKeyAndOrderFront_(None)

        # Click anywhere in another app -> dismiss (clicks on our own panel don't show up here)
        self.monitors.append(NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskLeftMouseDown, lambda e: self.dismiss()))
        self.monitors.append(NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, self._globalKey_))
        self.monitors.append(NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, self._localKey_))

    def _globalKey_(self, event):
        if event.keyCode() == KEY_ESC:
            self.dismiss()

    def _localKey_(self, event):
        if event.keyCode() == KEY_ESC:
            self.dismiss()
            return None
        return event

    def dismiss(self):
        for m in self.monitors:
            try:
                NSEvent.removeMonitor_(m)
            except Exception:
                pass
        self.monitors = []
        self.orderOut_(None)
        if self.onDismiss:
            self.onDismiss()

    def toneChosen_(self, sender):
        tone = TONES[sender.tag()]
        self.status.setStringValue_(f"Enhancing ({tone.lower()})…")
        threading.Thread(target=self._worker_, args=(tone,), daemon=True).start()

    def _worker_(self, tone):
        try:
            result = gemini_enhance(self.originalText, tone)
        except Exception as e:
            log.exception("enhancement failed")
            AppHelper.callAfter(self._failed_, str(e))
            return
        log.info("enhancement succeeded")
        AppHelper.callAfter(self._finished_, result)

    def _finished_(self, result):
        callback = self.onResult
        self.dismiss()
        log.info("tone panel dismissed before replacement")
        if callback:
            callback(result)

    def _failed_(self, msg):
        log.error("showing enhancement error: %s", msg)
        self.status.setStringValue_("Error: " + msg[:120])

# MARK: - App delegate

class AppDelegate(NSObject):
    def init(self):
        self = objc.super(AppDelegate, self).init()
        self.panel = None
        self.event_tap = None
        self.event_source = None
        self.event_callback = hotkey_event_callback
        return self

    def applicationDidFinishLaunching_(self, note):
        log.info("application launched")
        item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        item.button().setTitle_("✍️")
        menu = NSMenu.alloc().init()
        menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Select text anywhere, then press ⌥⌘E", None, ""))
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit", "terminate:", "q"))
        item.setMenu_(menu)

        # Prompts for Accessibility permission on first launch
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})

        global hotkey_delegate
        hotkey_delegate = self
        mask = CGEventMaskBit(kCGEventKeyDown)
        self.event_tap = CGEventTapCreate(
            kCGHIDEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly,
            mask, self.event_callback, None)
        if self.event_tap is None:
            log.error("could not create event tap")
            show_banner("Allow ToneKit under System Settings → Privacy & Security → Accessibility")
            return
        self.event_source = CFMachPortCreateRunLoopSource(None, self.event_tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), self.event_source, kCFRunLoopCommonModes)
        CGEventTapEnable(self.event_tap, True)
        log.info("event tap installed")

    def _handleHotKey(self):
        log.info("handling hotkey trusted=%s", AXIsProcessTrusted())
        if self.panel is not None:          # hotkey again = close popup
            self.panel.dismiss()
            return
        if not AXIsProcessTrusted():
            show_banner("Grant Accessibility: System Settings → Privacy & Security → Accessibility")
            return
        text = capture_selected_text()
        log.info("captured text=%s", bool(text))
        if not text:
            show_banner("No text selected — select some text first")
            return
        try:
            p = TonePanel.alloc().initWithText_(text)
        except Exception:
            log.exception("tone panel creation failed")
            show_banner("Could not open the tone popup")
            return
        p.onResult = self.replaceSelection_
        p.onDismiss = lambda: setattr(self, "panel", None)
        self.panel = p
        p.present()
        log.info("tone panel presented")

    def replaceSelection_(self, new_text):
        try:
            log.info("replacing selected text")
            saved = get_clipboard_text()
            set_clipboard_text(new_text)
            press_cmd_key(KEY_V)
            def restore():
                AppHelper.callAfter(lambda: set_clipboard_text(saved) if saved is not None else None)
            threading.Timer(0.4, restore).start()
            log.info("replacement key event sent")
        except Exception:
            log.exception("replacement failed")

# MARK: - Entry point

def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # menu bar only
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()

if __name__ == "__main__":
    main()
