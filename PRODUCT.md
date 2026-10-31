# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

ClipForge serves people assembling short compilations or montages from linked and local video without a timeline editor. This is inferred from the existing README and app controls.

## Product Purpose

Collect sources, choose an editing mode and export a local MP4. The app also offers a no-download stream player.

## Operating Context

The same local server runs in a native desktop window or browser. Captured links can arrive through a browser extension or bookmarklet. Downloads, exports and settings live in per-user storage outside the repository.

## Capabilities and Constraints

Keep Stitch, Smart cut and Montage, source quality, output settings, reorder and trim, captured-link queue, local files, browser capture, project storage, playback and render progress available. Processing stays local. The existing FastAPI endpoints and media pipeline remain the source of truth.

## Evidence on Hand

The repository includes original demonstration media in `docs/` and functioning source in `app.py`, `clipforge/` and `static/`.

## Product Principles

- Make the next source, edit and export action easy to find.
- Keep processing and storage status explicit.
- Use familiar desktop controls for repeat work.
