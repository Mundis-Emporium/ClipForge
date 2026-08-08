# ClipForge Capture (browser extension)

One-click send the current tab's video to your local ClipForge app, with no copy and paste.

## Install (Chrome / Edge / Brave)
1. Start ClipForge (`run.bat` or `python app.py`).
2. Go to `chrome://extensions`, enable **Developer mode**.
3. **Load unpacked** → select this `extension/` folder.
4. Pin the ClipForge icon. On any page with a video, click it (or right-click →
   **Add to ClipForge**). The link lands in ClipForge's capture inbox.
5. In ClipForge, hit **⬇ Pull captured** to load them, pick a mode, forge.

## Using it in a private / incognito window

Chrome turns extensions off in incognito by default, so you will only see the
bookmarklet there, not the toolbar button. To allow it: open
`chrome://extensions`, click **Details** on ClipForge Capture, and turn on
**Allow in Incognito**. The button then appears in private windows too.

No copy/paste needed. Works alongside the drag-to-bookmarks **bookmarklet** shown
in the app for browsers where you'd rather not install anything.
