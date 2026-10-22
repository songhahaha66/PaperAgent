# Frontend theme and work runtime

`theme.css` owns the palette, typography, geometry and the mapping to TDesign variables.
`studio.css` contains shared component and accessibility rules. Page and component styles
consume tokens; they do not redefine theme values. Set `theme-mode="light"` or
`theme-mode="dark"` on the root element. The default is light.

| Purpose | Token |
| --- | --- |
| Page, panel, secondary surface | `--pa-page`, `--pa-surface`, `--pa-surface-muted` |
| Hover and pressed surfaces | `--pa-surface-hover`, `--pa-surface-active` |
| Main, secondary, placeholder, disabled text | `--pa-ink`, `--pa-muted`, `--pa-placeholder`, `--pa-disabled` |
| Dividers and control outlines | `--pa-line`, `--pa-line-strong` |
| Primary action, hover, pressed | `--pa-accent`, `--pa-accent-hover`, `--pa-accent-active` |
| Selected surfaces and text on filled actions | `--pa-accent-soft`, `--pa-on-accent` |
| Information, annotations, warnings, errors | `--pa-info`, `--pa-note`, `--pa-warning`, `--pa-danger` and their soft backgrounds |
| Inline code and small labels | `--pa-radius-inline` (4px) |
| Buttons, form controls, navigation items | `--pa-radius-control` (8px) |
| Cards, file previews, message composers | `--pa-radius-card` (12px) |
| Dialogs and popup menus | `--pa-radius-overlay` (16px) |
| Pills and circles | `--pa-radius-pill`, `--pa-radius-circle` |
| Compact, standard, large controls | `--pa-control-height-sm/md/lg` (28/36/40px) |

Zero radius is allowed when a child sits flush inside a parent. Logo/image dimensions,
layout heights and text line heights are independent of control heights. Authored DOCX
pages deliberately use `--pa-document-paper` and `--pa-document-ink` in both themes;
the preview chrome follows the app theme. Syntax highlighting uses semantic colors.

## Work runtime boundaries

The keyed work route creates one lifetime per research workspace. `Work.vue` combines
these composables and renders the page:

- `useWorkSession`: work metadata, loading errors, authoritative snapshots, title updates,
  and initial load order (files/snapshot, history, task recovery, pending first message).
- `useWorkStream`: chat history/actions, socket events, reconnect/pause, recovery controls,
  elapsed time and attachment acknowledgements. It refreshes files through a callback.
- `useArtifactPreview`: shared file metadata, output selection, lazy figure blobs and downloads.
- `useWorkAttachments`: file input/reset, upload state and the completion callback.
- `useWorkLayout`: pane store integration, responsive layout, drafts and scroll restoration.

Server state is projected through the session; layout changes do not reconnect sockets.
Each resource owner handles its own unmount cleanup. Late server responses cannot mutate
a disposed session, late image blobs are revoked, and leaving cancels scheduled file refreshes.
Run `npm run test:workspace` for interaction, preview and resource-lifetime regressions.
