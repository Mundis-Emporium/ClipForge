# ClipForge interface

The app is a compact local editing workbench. A dark utility header anchors the identity; the task area uses a cool neutral canvas, white panels, dark ink and a single rust accent for selection and primary action.

## Layout

- Source input and edit/export controls form the wide working column.
- Capture, browser integration and project storage form the supporting column.
- At narrow widths the columns stack; small screens use full-width controls.

## Controls

- Browser-native selects, file input, checkboxes and disclosure rows remain recognizable.
- Mode choices are buttons with `aria-pressed` state.
- Rust marks the selected mode, focus, and primary export action. Green, amber and red remain reserved for download and error status.
- Labels use short action words; supporting explanations use a quieter text weight.

## Implementation

`static/workbench.css` contains the current visual system. `static/index.html` retains the app structure and behavior. New UI work should preserve control IDs used by the existing JavaScript and API.
