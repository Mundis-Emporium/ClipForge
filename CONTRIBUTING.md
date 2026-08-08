# Contributing to ClipForge

Thanks for taking a look. Bug reports, ideas, and pull requests are all welcome.

## Reporting a bug

Open an issue with:

- What you did and what you expected.
- What happened instead, including any error text from the log.
- Your OS, and whether you used the prebuilt app or ran from source.

The log lives with the app data at `%LOCALAPPDATA%\ClipForge\server.log` on Windows.

## Development setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

You need FFmpeg on your PATH when running from source.

Layout:

- `app.py` is the web server and API.
- `clipforge/` holds the pipeline: downloading, media helpers, smart cut, beat and onset detection, and the job runner.
- `static/index.html` is the whole UI in one file.
- `desktop.py` and `clipforge.spec` are for the packaged desktop build.

## Pull requests

- Keep changes focused, and describe what you changed and why.
- Match the style of the code around you.
- If you change the pipeline, test each mode once (stitch, smart cut, and montage) before opening the PR.

## Scope

ClipForge is a local tool for making video compilations. Please only use it for content you have the right to download and edit.
