# pyrefly: ignore [missing-import]
import sublime
# pyrefly: ignore [missing-import]
import sublime_plugin
import sys
import os
import re
import html  
import struct
import zlib
import base64

try:
    for k in list(sys.modules.keys()):
        if k.endswith('dwrite_render'):
            del sys.modules[k]
    from .dwrite_render import get_line_image_d2d
    _HAS_D2D = True
except Exception as e:
    print("RTLPreview: Direct2D renderer failed to load:", e)
    _HAS_D2D = False

# --- Windows GDI image renderer (for connected Arabic in phantoms) ---
try:
    import ctypes
    import ctypes.wintypes as _wt
    _gdi32 = ctypes.windll.gdi32
    _user32 = ctypes.windll.user32
    _HAS_GDI = True
except (AttributeError, OSError, ImportError):
    _HAS_GDI = False

# Uniscribe (usp10.dll) for BiDi-aware cursor positioning
try:
    _usp10 = ctypes.windll.usp10
    _HAS_USP = True
except (AttributeError, OSError, ImportError):
    _HAS_USP = False

if _HAS_GDI:
    # GDI constants
    _TRANSPARENT = 1
    _DEFAULT_CHARSET = 1
    _LOGPIXELSY = 90
    _ANTIALIASED_QUALITY = 4
    _CLEARTYPE_QUALITY = 5
    _DT_SINGLELINE = 0x20
    _DT_NOPREFIX = 0x800
    _DT_RTLREADING = 0x20000
    _DT_RIGHT = 0x02
    _DT_CALCRECT = 0x400
    _SSA_GLYPHS = 0x0010
    _SSA_FALLBACK = 0x0020

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ('biSize', _wt.DWORD), ('biWidth', ctypes.c_long),
            ('biHeight', ctypes.c_long), ('biPlanes', _wt.WORD),
            ('biBitCount', _wt.WORD), ('biCompression', _wt.DWORD),
            ('biSizeImage', _wt.DWORD), ('biXPelsPerMeter', ctypes.c_long),
            ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', _wt.DWORD),
            ('biClrImportant', _wt.DWORD),
        ]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [('bmiHeader', _BITMAPINFOHEADER)]

    class _GCP_RESULTS(ctypes.Structure):
        _fields_ = [
            ('lStructSize', _wt.DWORD),
            ('lpOutString', ctypes.c_void_p),
            ('lpOrder', ctypes.c_void_p),
            ('lpDx', ctypes.c_void_p),
            ('lpCaretPos', ctypes.c_void_p),
            ('lpClass', ctypes.c_void_p),
            ('lpGlyphs', ctypes.c_void_p),
            ('nGlyphs', ctypes.c_uint),
            ('nMaxFit', ctypes.c_int),
        ]
    _GCP_REORDER = 0x0002


def _make_png(w, h, bgra_buf):
    """Minimal PNG encoder: 32bpp BGRA top-down pixel buffer -> RGB PNG bytes."""
    def _chunk(tag, data):
        c = tag + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)

    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = _chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))

    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)  # filter: None
        base = y * stride
        for x in range(w):
            p = base + x * 4
            raw.append(bgra_buf[p + 2])  # R
            raw.append(bgra_buf[p + 1])  # G
            raw.append(bgra_buf[p])      # B

    idat = _chunk(b'IDAT', zlib.compress(bytes(raw), 9))
    iend = _chunk(b'IEND', b'')
    return sig + ihdr + idat + iend


# Per-line image cache: text -> data URI
_gdi_cache = {}


def _get_char_type(ch):
    o = ord(ch)
    if (0x0590 <= o <= 0x05FF): return 2 # R
    if (0x0600 <= o <= 0x08FF) or (0xFB1D <= o <= 0xFDFF) or (0xFE70 <= o <= 0xFEFE): return 3 # AL
    if (0x0030 <= o <= 0x0039): return 4 # EN
    if (0x0660 <= o <= 0x0669) or (0x06F0 <= o <= 0x06F9): return 5 # AN
    if ch in ' \t\n\r': return 6 # WS
    if (0x0041 <= o <= 0x005A) or (0x0061 <= o <= 0x007A) or (0x00C0 <= o <= 0x024F): return 1 # L
    return 7 # ON

def _split_bidi_runs(text):
    """Split text into BiDi runs and return them in VISUAL order from left to right."""
    if not text:
        return []
    
    types = [_get_char_type(ch) for ch in text]
    n = len(types)
    
    # 1. Base direction (assume LTR for code editor context)
    base_level = 0
            
    # 2. Resolve weak types (W1-W7)
    last_strong = None
    for i in range(n):
        t = types[i]
        if t in (1, 2, 3):
            last_strong = t
        elif t == 4 and last_strong == 3:
            types[i] = 5
            
    for i in range(n):
        if types[i] == 3:
            types[i] = 2
            
    for i in range(1, n-1):
        if types[i] in (7, 6):
            if types[i-1] == 4 and types[i+1] == 4:
                types[i] = 4
            elif types[i-1] == 5 and types[i+1] == 5:
                types[i] = 5
                
    last_strong = 1 if base_level == 0 else 2
    for i in range(n):
        t = types[i]
        if t in (1, 2):
            last_strong = t
        elif t == 4 and last_strong == 1:
            types[i] = 1
            
    # 3. Resolve Neutrals
    for i in range(n):
        if types[i] in (6, 7):
            prev_strong = base_level
            for j in range(i-1, -1, -1):
                if types[j] in (1, 2, 4, 5):
                    prev_strong = 2 if types[j] in (2, 4, 5) else 1
                    break
            if prev_strong == base_level:
                prev_strong = 2 if base_level == 1 else 1
                
            next_strong = base_level
            for j in range(i+1, n):
                if types[j] in (1, 2, 4, 5):
                    next_strong = 2 if types[j] in (2, 4, 5) else 1
                    break
            if next_strong == base_level:
                next_strong = 2 if base_level == 1 else 1
                
            if prev_strong == next_strong:
                types[i] = prev_strong
            else:
                types[i] = 2 if base_level == 1 else 1

    # 4. Implicit levels
    levels = []
    for t in types:
        if base_level == 0:
            if t == 1: levels.append(0)
            elif t == 2: levels.append(1)
            elif t in (4, 5): levels.append(2)
            else: levels.append(0)
        else:
            if t == 2: levels.append(1)
            elif t in (4, 5): levels.append(2)
            elif t == 1: levels.append(2)
            else: levels.append(1)
            
    # 5. Split into runs by level
    runs = []
    curr_level = levels[0]
    curr_start = 0
    for i in range(1, n):
        if levels[i] != curr_level:
            is_rtl = (curr_level % 2) != 0
            runs.append((is_rtl, text[curr_start:i], curr_start, i, curr_level))
            curr_level = levels[i]
            curr_start = i
    is_rtl = (curr_level % 2) != 0
    runs.append((is_rtl, text[curr_start:], curr_start, n, curr_level))
    
    # 6. Reorder runs visually
    max_level = max(levels)
    visual_runs = list(runs)
    for lvl in range(max_level, 0, -1):
        new_visual = []
        seq = []
        for r in visual_runs:
            if r[4] >= lvl:
                seq.append(r)
            else:
                if seq:
                    new_visual.extend(reversed(seq))
                    seq = []
                new_visual.append(r)
        if seq:
            new_visual.extend(reversed(seq))
        visual_runs = new_visual
        
    return [(r[0], r[1], r[2], r[3]) for r in visual_runs]


def _get_bidi_caret_x(hdc, text, cursor_pos):
    """Calculate exact visual caret X position for a given Python character cursor_pos (0..len(text)).
    Handles BiDi text (LTR/RTL runs), emojis, variation selectors, and font fallback accurately."""
    if cursor_pos is None or cursor_pos < 0 or cursor_pos > len(text):
        return None

    sz = _wt.SIZE()
    runs = _split_bidi_runs(text)
    curr_x = 0

    for is_rtl, run_text, start_idx, end_idx in runs:
        run_text_dir = "\u200F" + run_text + "\u200F" if is_rtl else "\u200E" + run_text + "\u200E"
        _gdi32.GetTextExtentPoint32W(hdc, run_text_dir, _utf16_len(run_text_dir), ctypes.byref(sz))
        run_w = sz.cx

        if start_idx <= cursor_pos <= end_idx:
            rel_pos = cursor_pos - start_idx
            if not is_rtl:
                prefix = run_text[:rel_pos]
                pref_ltr = "\u200E" + prefix + "\u200E" if prefix else prefix
                _gdi32.GetTextExtentPoint32W(hdc, pref_ltr, _utf16_len(pref_ltr), ctypes.byref(sz))
                return curr_x + sz.cx
            else:
                if rel_pos <= 0:
                    return curr_x + run_w
                if rel_pos >= len(run_text):
                    return curr_x

                suffix = run_text[rel_pos:]
                suff_rtl = "\u200F" + suffix + "\u200F" if suffix else suffix
                _gdi32.GetTextExtentPoint32W(hdc, suff_rtl, _utf16_len(suff_rtl), ctypes.byref(sz))
                return curr_x + sz.cx

        curr_x += run_w

    return curr_x


def _utf16_len(text):
    """Return the number of UTF-16 code units for a Python string."""
    n = 0
    for ch in text:
        n += 2 if ord(ch) > 0xFFFF else 1
    return n


def _py_to_utf16_pos(text, py_pos):
    """Convert a Python string index to a UTF-16 code unit index."""
    u16 = 0
    for i in range(min(py_pos, len(text))):
        u16 += 2 if ord(text[i]) > 0xFFFF else 1
    return u16


def _is_modifier_char(ch):
    """Return True if ch is a combining mark, variation selector, or ZWJ."""
    o = ord(ch)
    return (
        (0xFE00 <= o <= 0xFE0F) or    # Variation Selectors
        (0x0300 <= o <= 0x036F) or     # Combining Diacritical Marks
        (0x0610 <= o <= 0x061A) or     # Arabic combining marks
        (0x064B <= o <= 0x065F) or     # Arabic diacritics (tashkeel)
        (0x0670 == o) or               # Arabic superscript alef
        (0x06D6 <= o <= 0x06ED) or     # Arabic extended combining
        (0x1AB0 <= o <= 0x1AFF) or     # Combining Diacritical Marks Extended
        (0x1DC0 <= o <= 0x1DFF) or     # Combining Diacritical Marks Supplement
        (0x20D0 <= o <= 0x20FF) or     # Combining Marks for Symbols
        (0xFE20 <= o <= 0xFE2F) or     # Combining Half Marks
        o == 0x200D or                 # Zero-Width Joiner (emoji sequences)
        (0x1F3FB <= o <= 0x1F3FF) or   # Emoji Skin Tone Modifiers
        o == 0x200C                    # Zero-Width Non-Joiner
    )


def _snap_cursor_past_modifiers(text, pos):
    """If pos lands on a modifier/combining character, advance to the next base char."""
    while pos < len(text) and _is_modifier_char(text[pos]):
        pos += 1
    return pos


def _render_gdi_image(text, font_name, font_size, fg_rgb, bg_rgb, cursor_pos=None, cursor_rgb=(255, 255, 255), selection=None, sel_rgb=(62, 72, 73)):
    """Render a line of text as a PNG using Windows GDI and return a data: URI.
    GDI handles Arabic shaping natively, so letters come out connected.
    Returns None on failure."""
    if not _HAS_GDI or not text or not text.strip():
        return None

    hdc_screen = _user32.GetDC(0)
    if not hdc_screen:
        return None
    hdc = _gdi32.CreateCompatibleDC(hdc_screen)
    if not hdc:
        _user32.ReleaseDC(0, hdc_screen)
        return None

    result = None
    hfont = None
    hbmp = None
    try:
        dpi = _gdi32.GetDeviceCaps(hdc_screen, _LOGPIXELSY) or 96
        log_h = -int(font_size * dpi / 72)

        hfont = _gdi32.CreateFontW(
            log_h, 0, 0, 0, 400,
            0, 0, 0,
            _DEFAULT_CHARSET, 0, 0, _CLEARTYPE_QUALITY, 0,
            font_name
        )
        if not hfont:
            return None
        old_font = _gdi32.SelectObject(hdc, hfont)

        # Verify GDI actually loaded the requested font (not a substitution)
        buf = ctypes.create_unicode_buffer(64)
        _gdi32.GetTextFaceW(hdc, 64, buf)
        actual_face = buf.value
        if actual_face and actual_face.lower() != font_name.lower():
            # Font not found — fall back to Consolas (Sublime's default on Windows)
            _gdi32.SelectObject(hdc, old_font)
            _gdi32.DeleteObject(hfont)
            hfont = _gdi32.CreateFontW(
                log_h, 0, 0, 0, 400,
                0, 0, 0,
                _DEFAULT_CHARSET, 0, 0, _CLEARTYPE_QUALITY, 0,
                'Consolas'
            )
            if not hfont:
                return None
            old_font = _gdi32.SelectObject(hdc, hfont)

        # Measure text extent using DrawTextW (-1 = use null terminator,
        # handles surrogate pairs and combining chars correctly)
        flags = _DT_SINGLELINE | _DT_NOPREFIX
        rc = _wt.RECT(0, 0, 4000, 400)
        _user32.DrawTextW(hdc, text, -1, ctypes.byref(rc), flags | _DT_CALCRECT)

        pad_x, pad_y = 0, 0
        w = rc.right + pad_x * 2
        h = rc.bottom + pad_y * 2
        if w <= 0 or h <= 0:
            _gdi32.SelectObject(hdc, old_font)
            return None

        # Create 32bpp top-down DIB section
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB

        bits_ptr = ctypes.c_void_p()
        hbmp = _gdi32.CreateDIBSection(
            hdc, ctypes.byref(bmi), 0, ctypes.byref(bits_ptr), None, 0
        )
        if not hbmp or not bits_ptr.value:
            _gdi32.SelectObject(hdc, old_font)
            return None
        old_bmp = _gdi32.SelectObject(hdc, hbmp)

        # Fill background colour
        bg_ref = bg_rgb[0] | (bg_rgb[1] << 8) | (bg_rgb[2] << 16)
        hbr = _gdi32.CreateSolidBrush(bg_ref)
        fill_rc = _wt.RECT(0, 0, w, h)
        _user32.FillRect(hdc, ctypes.byref(fill_rc), hbr)
        _gdi32.DeleteObject(hbr)

        # Draw selection background per BiDi run to prevent cross-run bounding box artifacts
        if selection is not None:
            s_start, s_end = selection
            sel_ref = sel_rgb[0] | (sel_rgb[1] << 8) | (sel_rgb[2] << 16)
            hbr_sel = _gdi32.CreateSolidBrush(sel_ref)
            
            runs = _split_bidi_runs(text)
            curr_x = 0
            sz = _wt.SIZE()
            
            for is_rtl, run_text, start_idx, end_idx in runs:
                run_text_dir = "\u200F" + run_text + "\u200F" if is_rtl else "\u200E" + run_text + "\u200E"
                _gdi32.GetTextExtentPoint32W(hdc, run_text_dir, _utf16_len(run_text_dir), ctypes.byref(sz))
                run_w = sz.cx
                
                overlap_start = max(s_start, start_idx)
                overlap_end = min(s_end, end_idx)
                
                if overlap_start < overlap_end:
                    def get_x_in_run(pos):
                        rel_pos = pos - start_idx
                        if not is_rtl:
                            prefix = run_text[:rel_pos]
                            pref_ltr = "\u200E" + prefix + "\u200E" if prefix else prefix
                            _gdi32.GetTextExtentPoint32W(hdc, pref_ltr, _utf16_len(pref_ltr), ctypes.byref(sz))
                            return curr_x + sz.cx
                        else:
                            if rel_pos <= 0: return curr_x + run_w
                            if rel_pos >= len(run_text): return curr_x
                            suffix = run_text[rel_pos:]
                            suff_rtl = "\u200F" + suffix + "\u200F" if suffix else suffix
                            _gdi32.GetTextExtentPoint32W(hdc, suff_rtl, _utf16_len(suff_rtl), ctypes.byref(sz))
                            return curr_x + sz.cx
                            
                    x1 = get_x_in_run(overlap_start)
                    x2 = get_x_in_run(overlap_end)
                    
                    left = min(x1, x2) + pad_x
                    right = max(x1, x2) + pad_x
                    if left != right:
                        sel_rc = _wt.RECT(left, pad_y, right, h - pad_y)
                        _user32.FillRect(hdc, ctypes.byref(sel_rc), hbr_sel)
                        
                curr_x += run_w
                
            _gdi32.DeleteObject(hbr_sel)

        # Draw text (GDI handles Arabic shaping + bidi natively)
        fg_ref = fg_rgb[0] | (fg_rgb[1] << 8) | (fg_rgb[2] << 16)
        _gdi32.SetTextColor(hdc, fg_ref)
        _gdi32.SetBkMode(hdc, _TRANSPARENT)

        draw_rc = _wt.RECT(pad_x, pad_y, w - pad_x, h - pad_y)
        _user32.DrawTextW(
            hdc, text, -1, ctypes.byref(draw_rc),
            flags
        )

        # Draw cursor caret overlay at the correct visual position.
        # Uses BiDi run measurement with GDI font fallback text extent
        # to ensure emojis, variation selectors, and surrogate pairs
        # do not introduce offset errors.
        if cursor_pos is not None and 0 <= cursor_pos <= len(text) and len(text) > 0:
            cursor_pos = _snap_cursor_past_modifiers(text, cursor_pos)
            cursor_x = _get_bidi_caret_x(hdc, text, cursor_pos)
            if cursor_x is not None:
                cursor_x += pad_x
                cursor_x = max(pad_x, min(cursor_x, w - pad_x))

                cursor_ref = cursor_rgb[0] | (cursor_rgb[1] << 8) | (cursor_rgb[2] << 16)
                hbr_cursor = _gdi32.CreateSolidBrush(cursor_ref)
                caret_rc = _wt.RECT(cursor_x, pad_y, cursor_x + 1, h - pad_y)
                _user32.FillRect(hdc, ctypes.byref(caret_rc), hbr_cursor)
                _gdi32.DeleteObject(hbr_cursor)

        _gdi32.GdiFlush()

        # Read pixel buffer
        buf_size = w * h * 4
        pixels = (ctypes.c_ubyte * buf_size)()
        ctypes.memmove(pixels, bits_ptr, buf_size)

        # Cleanup GDI objects
        _gdi32.SelectObject(hdc, old_bmp)
        _gdi32.SelectObject(hdc, old_font)
        _gdi32.DeleteObject(hbmp)
        hbmp = None
        _gdi32.DeleteObject(hfont)
        hfont = None

        # Encode as PNG and return data URI
        png_bytes = _make_png(w, h, pixels)
        b64 = base64.b64encode(png_bytes).decode('ascii')
        result = 'data:image/png;base64,' + b64

    except Exception:
        # Silently fail — text fallback will be used
        pass
    finally:
        # Ensure cleanup of any remaining GDI objects
        if hfont:
            _gdi32.DeleteObject(hfont)
        if hbmp:
            _gdi32.DeleteObject(hbmp)
        _gdi32.DeleteDC(hdc)
        _user32.ReleaseDC(0, hdc_screen)

    return result


def _get_line_image(text, font_name='Consolas', font_size=10, fg_rgb=(152, 195, 121), bg_rgb=(43, 48, 60), cursor_rgb=(255, 255, 255), sel_rgb=(62, 72, 73), cursor_pos=None, selection=None):
    """Return a cached or freshly rendered data: URI for a line of text."""
    cache_key = (text, font_name, font_size, fg_rgb, bg_rgb, cursor_rgb, sel_rgb, cursor_pos, selection)
    if cache_key in _gdi_cache:
        return _gdi_cache[cache_key]

    if len(_gdi_cache) > 500:
        _gdi_cache.clear()

    uri = _render_gdi_image(text, font_name, font_size, fg_rgb, bg_rgb, cursor_pos=cursor_pos, cursor_rgb=cursor_rgb, selection=selection, sel_rgb=sel_rgb)
    if uri:
        _gdi_cache[cache_key] = uri
    return uri


# --- RTL detection helpers ---

_A = r'\u0590-\u05ff\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff'
_S = r'0-9\s/.,!?\-%'
RTL_RE = re.compile(r'(?:[' + _A + _S + r'])*[' + _A + r'](?:[' + _A + _S + r'])*')

GLOBAL_PHANTOMS = {}

_HTML_CACHE = {}


def get_phantom_set(view):
    view_id = view.id()
    if view_id not in GLOBAL_PHANTOMS:
        GLOBAL_PHANTOMS[view_id] = sublime.PhantomSet(view, "rtl_preview")
    return GLOBAL_PHANTOMS[view_id]


def check_libs():
    """Ensure the environment is capable of rendering."""
    if not _HAS_GDI and not _HAS_D2D:
        sublime.error_message(
            "RTLPreview Error:\n"
            "This plugin requires a Windows environment to run (GDI or Direct2D)."
        )
        return False
    return True


def has_rtl(text):
    for char in text:
        o = ord(char)
        if (0x0590 <= o <= 0x05FF) or \
           (0x0600 <= o <= 0x06FF) or \
           (0x0750 <= o <= 0x077F) or \
           (0x08A0 <= o <= 0x08FF) or \
           (0xFB50 <= o <= 0xFDFF) or \
           (0xFE70 <= o <= 0xFEFF):
            return True
    return False


# --- Phantom rendering ---

def _make_image_phantom_html(data_uri):
    """Build a phantom HTML that displays a rendered PNG image."""
    return (
        '<body id="rtl-phantom">'
        '  <style>'
        '    .rtl-img {'
        '      margin-top: 2px;'
        '      margin-bottom: 2px;'
        '    }'
        '  </style>'
        '  <div class="rtl-img">'
        '    <img src="%s" />'
        '  </div>'
        '</body>'
    ) % data_uri


def update_view_phantoms(view):
    """Generates and displays inline RTL phantoms below lines containing RTL characters.
    Prefers GDI-rendered images (connected Arabic). Falls back to shaped text."""
    if not view.settings().get('rtl_phantom_mode', False):
        return

    plugin_settings = sublime.load_settings('RTLPreview.sublime-settings')
    mode = plugin_settings.get('process_mode', 'full_line')
    show_cursor = plugin_settings.get('show_cursor', True)
    show_selection = plugin_settings.get('show_selection', True)
    show_gutter_icon = plugin_settings.get('show_gutter_icon', True)
    process_only_visible = plugin_settings.get('process_only_visible', True)
    rendering_method = plugin_settings.get('rendering_method', 'direct2d')
    
    if rendering_method == 'direct2d' and not _HAS_D2D:
        rendering_method = 'gdi'
    if rendering_method == 'gdi' and not _HAS_GDI:
        # Cannot render anything
        return
    
    view_font_size = plugin_settings.get('phantom_font_size', 0)
    if not view_font_size:
        view_font_size = view.settings().get('font_size', 10)
        
    view_font_name = plugin_settings.get('phantom_font', "")
    if not view_font_name:
        view_font_name = view.settings().get('font_face', '') or 'Consolas'

    try:
        style = view.style()
        bg_hex = style.get('background', '#2b303c')
        fg_hex = style.get('foreground', '#98c379')
        caret_hex = style.get('caret', '#ffffff')
        sel_hex = style.get('selection', '#3e4849')
    except AttributeError:
        # Fallback for very old Sublime Text versions
        bg_hex = '#2b303c'
        fg_hex = '#98c379'
        caret_hex = '#ffffff'
        sel_hex = '#3e4849'

    def parse_hex(h, default):
        if not h or not h.startswith('#'): return default
        h = h.lstrip('#')
        if len(h) == 3: h = "".join([c*2 for c in h])
        if len(h) >= 6:
            try: return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            except ValueError: pass
        return default

    bg_rgb = parse_hex(bg_hex, (43, 48, 60))
    fg_rgb = parse_hex(fg_hex, (152, 195, 121))
    caret_rgb = parse_hex(caret_hex, (255, 255, 255))
    sel_rgb = parse_hex(sel_hex, (62, 72, 73))

    sel = view.sel()
    cursor_pt = sel[0].b if sel and len(sel) > 0 else -1

    phantoms = []
    gutter_regions = []
    
    if process_only_visible:
        vis_reg = view.visible_region()
        # Expand by roughly 50 lines (approx 3000 chars) as a buffer
        start = max(0, vis_reg.begin() - 3000)
        end = min(view.size(), vis_reg.end() + 3000)
        target_region = sublime.Region(start, end)
    else:
        target_region = sublime.Region(0, view.size())
        
    lines = view.lines(target_region)

    for line_reg in lines:
        line_text = view.substr(line_reg)
        if has_rtl(line_text):
            line_fg_hex = fg_hex
            try:
                for i, char in enumerate(line_text):
                    if has_rtl(char):
                        pt = line_reg.begin() + i
                        scope = view.scope_name(pt)
                        scope_style = view.style_for_scope(scope)
                        if 'foreground' in scope_style:
                            line_fg_hex = scope_style['foreground']
                        break
            except AttributeError:
                pass
            line_fg_rgb = parse_hex(line_fg_hex, fg_rgb)

            # Strip leading whitespace and anchor at the indented position
            stripped = line_text.lstrip()
            indent_len = len(line_text) - len(stripped)
            line_begin = line_reg.begin()
            pos = line_begin + indent_len

            # Determine if the cursor is on this line and calculate relative offset
            cursor_pos_in_stripped = None
            selection_in_stripped = None
            if line_begin <= cursor_pt <= line_reg.end():
                rel_cursor = cursor_pt - pos
                if rel_cursor < 0:
                    rel_cursor = 0
                elif rel_cursor > len(stripped):
                    rel_cursor = len(stripped)
                cursor_pos_in_stripped = rel_cursor

            if sel and len(sel) > 0:
                sel_min = min(sel[0].a, sel[0].b)
                sel_max = max(sel[0].a, sel[0].b)
                if sel_max > pos and sel_min < line_reg.end():
                    s_start = max(0, sel_min - pos)
                    s_end = min(len(stripped), sel_max - pos)
                    if s_start < s_end:
                        selection_in_stripped = (s_start, s_end)

            if mode == 'arabic_only':
                rtl_parts = [match.group(0).strip() for match in RTL_RE.finditer(stripped)]
                if not rtl_parts:
                    continue
                stripped = " ... ".join(rtl_parts)
                cursor_pos_in_stripped = None
                selection_in_stripped = None

            html_content = None
            
            # Use cache to avoid generating the exact same image over and over
            cache_key = (
                stripped, view_font_name, view_font_size, line_fg_hex, bg_hex, caret_hex, sel_hex,
                cursor_pos_in_stripped if show_cursor else None,
                selection_in_stripped if show_selection else None,
                rendering_method
            )
            
            if cache_key in _HTML_CACHE:
                html_content = _HTML_CACHE[cache_key]
            else:
                # Try image rendering first (properly connected Arabic)
                if rendering_method in ('direct2d', 'gdi'):
                    pass_cursor = cache_key[7]
                    pass_selection = cache_key[8]
                    
                    try:
                        if rendering_method == 'direct2d':
                            data_uri = get_line_image_d2d(stripped, view_font_name, view_font_size, line_fg_rgb, bg_rgb, caret_rgb, sel_rgb, cursor_pos=pass_cursor, selection=pass_selection)
                        else:
                            data_uri = _get_line_image(stripped, view_font_name, view_font_size, line_fg_rgb, bg_rgb, caret_rgb, sel_rgb, cursor_pos=pass_cursor, selection=pass_selection)
                        
                        if data_uri:
                            html_content = _make_image_phantom_html(data_uri)
                    except Exception as e:
                        print("RTLPreview: Error rendering with %s: %s" % (rendering_method, e))
    
                if html_content:
                    _HTML_CACHE[cache_key] = html_content
                    # prevent infinite memory growth
                    if len(_HTML_CACHE) > 5000:
                        # Clear half the cache
                        keys_to_del = list(_HTML_CACHE.keys())[:2500]
                        for k in keys_to_del:
                            del _HTML_CACHE[k]

            if html_content:
                phantom = sublime.Phantom(
                    sublime.Region(pos, pos),
                    html_content,
                    sublime.LAYOUT_BELOW
                )
                phantoms.append(phantom)
                gutter_regions.append(line_reg)

    view_id = view.id()
    if phantoms:
        if view_id not in GLOBAL_PHANTOMS:
            GLOBAL_PHANTOMS[view_id] = sublime.PhantomSet(view, 'rtl_phantoms')
        GLOBAL_PHANTOMS[view_id].update(phantoms)
        
        if show_gutter_icon:
            view.add_regions('rtl_phantom_gutter', gutter_regions, 'region.greenish', 'bookmark', sublime.HIDDEN)
        else:
            view.erase_regions('rtl_phantom_gutter')
    else:
        if view_id in GLOBAL_PHANTOMS:
            GLOBAL_PHANTOMS[view_id].update([])
        view.erase_regions('rtl_phantom_gutter')


def clear_view_phantoms(view):
    """Removes all RTL phantoms and gutter icons from the view."""
    view_id = view.id()
    if view_id in GLOBAL_PHANTOMS:
        GLOBAL_PHANTOMS[view_id].update([])
        del GLOBAL_PHANTOMS[view_id]
    view.erase_regions('rtl_phantom_gutter')


# --- Status bar ---

_STATUS_KEY = 'rtl_preview'


def _update_status_bar(view):
    """Update the status bar indicator for RTL mode."""
    if view.settings().get('rtl_phantom_mode', False):
        view.set_status(_STATUS_KEY, 'RTL: ON')
    else:
        view.erase_status(_STATUS_KEY)


# --- Commands ---

class RtlToggleViewCommand(sublime_plugin.TextCommand):
    """Toggles in-place visual phantoms for reading RTL text directly without editing buffer."""
    def run(self, edit):
        if not _HAS_GDI and not _HAS_D2D:
            return

        settings = self.view.settings()
        is_active = settings.get('rtl_phantom_mode', False)

        if is_active:
            clear_view_phantoms(self.view)
            settings.erase('rtl_phantom_mode')
            sublime.status_message("RTL Visual Overlays Disabled")
        else:
            settings.set('rtl_phantom_mode', True)
            update_view_phantoms(self.view)
            sublime.status_message("RTL Visual Overlays Enabled")

        _update_status_bar(self.view)

    def is_checked(self):
        return self.view.settings().get('rtl_phantom_mode', False)


class RtlEventListener(sublime_plugin.EventListener):
    """Listens for document modifications and cursor movement to update phantoms in real-time."""

    def on_modified(self, view):
        if view.settings().get('rtl_phantom_mode', False):
            update_view_phantoms(view)

    def on_selection_modified(self, view):
        if view.settings().get('rtl_phantom_mode', False):
            update_view_phantoms(view)

    def on_activated(self, view):
        """Sync the status bar indicator when switching to a view."""
        _update_status_bar(view)

    def on_close(self, view):
        view_id = view.id()
        if view_id in GLOBAL_PHANTOMS:
            del GLOBAL_PHANTOMS[view_id]
