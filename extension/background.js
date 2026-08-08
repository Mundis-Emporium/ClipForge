// ClipForge Capture. MV3 service worker.
// Sends the PAGE URL to ClipForge, which extracts the real video (via yt-dlp).
// We deliberately do NOT send the <video> element's currentSrc: on many sites
// that is a blob: or a single-use, session-bound stream that fails to download
// from a different session. The page URL is what works everywhere.

const CLIPFORGE = "http://127.0.0.1:8765";

function send(url, tab) {
  if (!url) return;
  fetch(CLIPFORGE + "/api/inbox", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  })
    .then(r => r.json())
    .then(j => notify(`Captured (${j.total} in inbox)`, url))
    .catch(() => notify("ClipForge not running", "Start the ClipForge app first."));
}

function isPageLike(u) {
  return typeof u === "string" && /^https?:\/\//i.test(u) && !/^blob:/i.test(u);
}

async function capture(tab) {
  // always the page URL of the tab you're on
  send(tab && tab.url, tab);
}

function notify(title, msg) {
  chrome.notifications.create({
    type: "basic", iconUrl: "icon.png", title, message: String(msg).slice(0, 120),
  });
}

chrome.action.onClicked.addListener(capture);

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "clipforge-add", title: "Add to ClipForge",
    contexts: ["page", "video", "link"],
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  // right-click a link to another video page -> use that link;
  // otherwise use the page URL (NOT the raw video src, which is session-bound).
  let url = tab && tab.url;
  if (info.menuItemId === "clipforge-add" && info.linkUrl && isPageLike(info.linkUrl)) {
    url = info.linkUrl;
  }
  send(url, tab);
});
