# Linux comic chat-bubble plugin

The optional **Comic chat bubbles** plugin displays notification text beside
the pet. It omits the notification title and uses a white, black-ink rounded
speech balloon with a single pointed tail aimed at the pet. The tail joins the
balloon at the middle of its side; the border segment at the join is removed so
the pointer remains connected as the corner radius changes. The balloon is on
the side opposite the plugin status stack.

## Enable and behavior

- Enable **Plugins → Comic chat bubbles**. It is off by default and stored as
  `linuxPlugins.chatBubble`.
- Bubble width and height are computed from Pango's UTF-8 text metrics, the
  selected width-to-height ratio, and the available space beside the pet. Text
  wraps to the rounded shape; each line is centered within the width available
  at its vertical position. Newlines and Vietnamese shaping are measured by
  Pango rather than estimated from raw byte counts.
- Horizontal and vertical text padding are fixed at 12 px. The bubble uses a
  white background and black text in both light and dark themes.
- **Settings** exposes the comic font size, width-to-height ratio, and one
  corner-radius percentage (0–50%). That percentage is applied separately to
  the rendered width and height, producing horizontal and vertical radii.
- **Settings → Comic chat bubble opacity** adjusts the complete balloon
  (background, outline, pointer, and text) from fully transparent to opaque.
- **Settings → Comic chat bubble duration** independently controls how long a
  queued chat bubble stays visible (1–60 seconds). It does not change the
  lifetime of collapsed message previews.
- Pango size calculations and wrapped line layouts are bounded in-memory
  caches; repeated relayouts and redraws reuse the measurement instead of
  reshaping the same text on every frame.
- Every `notify` is normalized to `kind: "chat"`, including lifecycle updates.
  Its `status` is preserved for task animation and lifecycle behavior. Chat
  notifications enter a FIFO queue, including repeated updates on the same
  thread; one item is shown per configured chat-bubble duration, with up to 50
  pending items.
- Chat entries remain in notification history. Clicking the balloon opens
  notification history. Transparent areas remain click-through.

## Tests

```sh
PYTHONPATH=Linux env -u DISPLAY /usr/bin/python3 -m unittest Linux.tests.test_chat_bubble -v
PYTHONPATH=Linux env -u DISPLAY /usr/bin/python3 -m unittest discover -s Linux/tests
```

Geometry and Cairo rendering tests use in-memory surfaces and do not open a
window on the user's desktop.
