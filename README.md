# ToneKit

ToneKit is a macOS menu-bar utility that rewrites selected text with Gemini in a chosen tone.

## Requirements

- macOS
- Python 3.13 with PyObjC and requests installed
- A Gemini API key from Google AI Studio
- Accessibility permission for the Python runtime

## Setup

1. Create or copy a Gemini API key from:

   https://aistudio.google.com/apikey

2. Open Terminal and set the key for the current terminal session:

   ```bash
   export GEMINI_API_KEY="your-gemini-api-key"
   ```

3. Optional: choose a different Gemini model:

   ```bash
   export GEMINI_MODEL="gemini-2.5-flash"
   ```

4. Allow the Python runtime under **System Settings > Privacy & Security > Accessibility**.

## Run

From Terminal:

```bash
cd /Users/dhaneshbaheti/Documents/tonekit
/usr/local/bin/python3 tonekit.py
```

ToneKit appears in the menu bar. Select text in any app and press **Option + Command + E**. Choose a tone such as Formal, Academic, Fun, or Concise. The selected text is replaced with the enhanced version.

## Troubleshooting

- If the shortcut does nothing, confirm Accessibility permission for `/usr/local/bin/python3`, then quit and relaunch ToneKit.
- If the popup appears but rewriting fails, check that `GEMINI_API_KEY` is set in the same Terminal session used to launch the app.
- ToneKit writes diagnostic information to `/tmp/tonekit.log`.
- Do not put the API key in `tonekit.py` or commit it to source control.

## Stop ToneKit

Press `Control + C` in the Terminal running the app, or use **Quit** from the ToneKit menu-bar menu.
