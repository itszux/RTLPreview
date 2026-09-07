# Sublime Text RTL Preview

A feature-rich, high-performance Sublime Text plugin that provides full support for Right-to-Left (RTL) languages like Arabic, Persian, and Hebrew. 

![Preview 1](preview%201.png)
![Preview 2](preview%202.png)

Since Sublime Text does not natively support bidirectional text layout or complex script character shaping (like Arabic cursive joining), this plugin uses blazing-fast native Windows rendering APIs (Direct2D/GDI) to seamlessly render correctly shaped and aligned RTL text in real-time, right inside your editor, while keeping your files correctly ordered on disk.

---

## Key Features

1. **Inline Live Phantom Previews (New!)**  
   Automatically tracks your cursor and viewport to instantly render perfect, connected Arabic and RTL text directly below your active line using Sublime Text's Phantom API. It acts like a live mirror directly in your code.

2. **Native Direct2D & GDI Rendering**  
   Completely bypasses Sublime Text's limited `minihtml` engine by leveraging native Windows UI drawing APIs. This ensures perfect complex text shaping, flawless bidirectional (BiDi) layout, and native support for full-color emojis (via Direct2D).

3. **Perfect Caret & Selection Tracking**  
   The inline phantoms track your exact caret position and text selection in real-time, translating logical string indexes into pixel-perfect visual offsets, making it incredibly intuitive to edit Arabic/RTL strings.

4. **Split-Screen & In-Place Mirror**  
   In addition to inline phantoms, the plugin still supports opening a side-by-side read-only scratch view that renders shaped RTL text, or temporarily mirroring the current buffer in-place.

5. **Edit RTL Selection Command**  
   Select a block of RTL text and edit it in a native OS input panel at the bottom of the editor for a frictionless typing experience.

---

## Configuration Settings

You can fully customize the plugin by modifying the user settings file:
* **Preferences** > **Package Settings** > **RTL Preview** > **Settings**

### Rendering Engine (`rendering_method`)
* `"direct2d"` (Default): Modern Windows rendering. Native support for complex text layout and full-color emojis.
* `"gdi"`: Legacy Windows rendering. Slightly faster, but emojis are drawn as monochrome outlines.

### Processing Mode (`process_mode`)
* `"full_line"` (Default): Displays the entire line (including English text, punctuation, and code), relying on the native Windows engine to handle the bidirectional layout perfectly.
* `"arabic_only"`: Hides all English text and code syntax, displaying ONLY the extracted Arabic/RTL text chunks from the line.

### Other Customizations
* `"phantom_font"` & `"phantom_font_size"`: Override the default editor font specifically for Arabic rendering.
* `"show_cursor"` & `"show_selection"`: Toggle the live caret and selection highlighting in the phantoms.
* `"process_only_visible"`: Massively improves performance on huge files by aggressively caching and exclusively rendering text currently visible in your viewport.

### Key Bindings
By default, the plugin does not bind any keys to avoid conflicting with other packages. You can toggle the RTL previews via the Command Palette (`RTL Preview: Toggle Preview`). 

To add a custom shortcut, open **Preferences > Package Settings > RTL Preview > Key Bindings** and add the following:
```json
[
    { "keys": ["ctrl+f1"], "command": "rtl_toggle_view" }
]
```

---

## Installation

### Package Control (Recommended)
The easiest way to install RTL Preview is via Package Control.
1. Open the Command Palette (`Ctrl+Shift+P` on Windows/Linux, `Cmd+Shift+P` on Mac).
2. Type `Package Control: Install Package` and press Enter.
3. Search for `RTL Preview` and press Enter to install.
4. Restart Sublime Text.

### Manual Installation
You can also install the plugin manually by cloning this repository into your Sublime Text `Packages` directory:
1. Open Sublime Text and go to **Preferences** > **Browse Packages...**
2. Open a terminal in that directory and run:
   ```bash
   git clone https://github.com/itszux/RTLPreview.git
   ```
3. Restart Sublime Text.

---

## How It Works Under the Hood

Unlike previous attempts at RTL support that relied purely on Python string reversal (which fails to connect characters properly in standard LTR rendering engines), RTL Preview creates an invisible, off-screen Windows device context. 

As you type, it passes the logical string to Windows (using `ctypes` bindings to `dwrite.dll` or `gdi32.dll`), allowing the OS to perform perfect complex text shaping and BiDi layout. It then captures this perfectly rendered text as a highly-optimized PNG image in memory, caches it, and projects it instantly into the Sublime Text buffer via the Phantom API.

By managing the `scratch` state and hooking into Sublime's events, the plugin ensures that the visual representation in the editor is flawless, while your actual source code files remain standard Unicode and uncorrupted on disk.
