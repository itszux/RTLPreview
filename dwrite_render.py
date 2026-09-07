import ctypes
from ctypes import *
from ctypes.wintypes import *
import math

ole32 = ctypes.windll.ole32
d2d1 = ctypes.windll.d2d1
dwrite = ctypes.windll.dwrite

class GUID(Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", c_ubyte * 8)]

CLSID_WICImagingFactory = GUID(0xcacaf262, 0x9370, 0x4615, (c_ubyte*8)(0xa1, 0x3b, 0x9f, 0x55, 0x39, 0xda, 0x4c, 0xa))
IID_IWICImagingFactory = GUID(0xec5ec8a9, 0xc395, 0x4314, (c_ubyte*8)(0x9c, 0x77, 0x54, 0xd7, 0xa9, 0x35, 0xff, 0x70))
IID_IDWriteFactory = GUID(0xb859ee5a, 0xd838, 0x4b5b, (c_ubyte*8)(0xa2, 0xe8, 0x1a, 0xdc, 0x7d, 0x93, 0xdb, 0x48))
IID_ID2D1Factory = GUID(0x06152247, 0x6f50, 0x465a, (c_ubyte*8)(0x92, 0x45, 0x11, 0x8b, 0xfd, 0x3b, 0x60, 0x07))
GUID_WICPixelFormat32bppPBGRA = GUID(0x6fddc324, 0x4e03, 0x4bfe, (c_ubyte*8)(0xb1, 0x85, 0x3d, 0x77, 0x76, 0x8d, 0xc9, 0x10))

class D2D1_COLOR_F(Structure):
    _fields_ = [("r", c_float), ("g", c_float), ("b", c_float), ("a", c_float)]

class D2D1_POINT_2F(Structure):
    _fields_ = [("x", c_float), ("y", c_float)]

class D2D1_PIXEL_FORMAT(Structure):
    _fields_ = [("format", c_uint32), ("alphaMode", c_uint32)]

class D2D1_RENDER_TARGET_PROPERTIES(Structure):
    _fields_ = [("type", c_uint32), ("pixelFormat", D2D1_PIXEL_FORMAT), ("dpiX", c_float), ("dpiY", c_float), ("usage", c_uint32), ("minLevel", c_uint32)]

class WICRect(Structure):
    _fields_ = [("X", c_int), ("Y", c_int), ("Width", c_int), ("Height", c_int)]
    
class DWRITE_TEXT_METRICS(Structure):
    _fields_ = [
        ("left", c_float),
        ("top", c_float),
        ("width", c_float),
        ("widthIncludingTrailingWhitespace", c_float),
        ("height", c_float),
        ("layoutWidth", c_float),
        ("layoutHeight", c_float),
        ("maxBidiReorderingDepth", c_uint32),
        ("lineCount", c_uint32)
    ]
    
class DWRITE_HIT_TEST_METRICS(Structure):
    _fields_ = [
        ("textPosition", c_uint32),
        ("length", c_uint32),
        ("left", c_float),
        ("top", c_float),
        ("width", c_float),
        ("height", c_float),
        ("bidiLevel", c_uint32),
        ("isText", c_int),
        ("isTrimmed", c_int)
    ]

def call_com(obj, method_index, restype, *argtypes):
    if not obj or not getattr(obj, 'value', obj):
        raise ValueError("Null COM pointer")
    vtable_ptr = cast(obj, POINTER(c_void_p)).contents
    func_ptr_addr = vtable_ptr.value + method_index * sizeof(c_void_p)
    func_ptr = cast(func_ptr_addr, POINTER(c_void_p)).contents
    prototype = WINFUNCTYPE(restype, c_void_p, *argtypes)
    return cast(func_ptr, prototype)

def check_hr(hr, msg):
    if hr < 0:
        raise Exception("%s failed with HR: %s" % (msg, hex(hr & 0xffffffff)))

_wic_factory = None
_dwrite_factory = None
_d2d_factory = None

def init_factories():
    global _wic_factory, _dwrite_factory, _d2d_factory
    if _wic_factory:
        return

    ole32.CoInitialize(None)

    wic = c_void_p()
    check_hr(ole32.CoCreateInstance(byref(CLSID_WICImagingFactory), None, 1, byref(IID_IWICImagingFactory), byref(wic)), "CoCreateInstance WIC")
    _wic_factory = wic

    dwrite_f = c_void_p()
    check_hr(dwrite.DWriteCreateFactory(0, byref(IID_IDWriteFactory), byref(dwrite_f)), "DWriteCreateFactory")
    _dwrite_factory = dwrite_f

    d2d_f = c_void_p()
    check_hr(d2d1.D2D1CreateFactory(0, byref(IID_ID2D1Factory), None, byref(d2d_f)), "D2D1CreateFactory")
    _d2d_factory = d2d_f

import zlib
import struct

def _chunk(tag, data):
    return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

def _rgba_to_png(width, height, bgra_data):
    # D2D uses PBGRA. We need to convert it to RGBA for PNG.
    # WIC bitmap returns BGRA (blue, green, red, alpha).
    rgba = bytearray(bgra_data)
    rgba[0::4], rgba[2::4] = rgba[2::4], rgba[0::4]

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(rgba[y * width * 4 : (y + 1) * width * 4])

    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b'IHDR', struct.pack("!IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += _chunk(b'IDAT', zlib.compress(bytes(raw), 9))
    png += _chunk(b'IEND', b'')
    return png

import base64

class D2D1_RECT_F(Structure):
    _fields_ = [("left", c_float), ("top", c_float), ("right", c_float), ("bottom", c_float)]

_cached_text_format = None
_cached_font_name = None
_cached_font_size = 0

_cached_bitmap = None
_cached_rt = None
_cached_width = 0
_cached_height = 0

_cached_brush = None
_cached_sel_brush = None
_cached_caret_brush = None

def get_line_image_d2d(text, font_name, font_size, fg_rgb, bg_rgb, caret_rgb, sel_rgb, cursor_pos=None, selection=None):
    global _cached_text_format, _cached_font_name, _cached_font_size
    global _cached_bitmap, _cached_rt, _cached_width, _cached_height
    global _cached_brush, _cached_sel_brush, _cached_caret_brush
    
    init_factories()
    
    if _cached_text_format is None or _cached_font_name != font_name or _cached_font_size != font_size:
        if _cached_text_format:
            call_com(_cached_text_format, 2, c_ulong)(_cached_text_format)
            _cached_text_format = None
            
        text_format = c_void_p()
        CreateTextFormat = call_com(_dwrite_factory, 15, c_long, c_wchar_p, c_void_p, c_uint, c_uint, c_uint, c_float, c_wchar_p, POINTER(c_void_p))
        check_hr(CreateTextFormat(_dwrite_factory, font_name, None, 400, 0, 5, font_size * 1.33, "", byref(text_format)), "CreateTextFormat")
        _cached_text_format = text_format
        _cached_font_name = font_name
        _cached_font_size = font_size
    else:
        text_format = _cached_text_format
    
    text_layout = c_void_p()
    CreateTextLayout = call_com(_dwrite_factory, 18, c_long, c_wchar_p, c_uint, c_void_p, c_float, c_float, POINTER(c_void_p))
    check_hr(CreateTextLayout(_dwrite_factory, text, len(text), text_format, 10000.0, 10000.0, byref(text_layout)), "CreateTextLayout")
    
    metrics = DWRITE_HIT_TEST_METRICS()
    px, py = c_float(), c_float()
    HitTestTextPosition = call_com(text_layout, 65, c_long, c_uint, c_int, POINTER(c_float), POINTER(c_float), POINTER(DWRITE_HIT_TEST_METRICS))
    
    t_metrics = DWRITE_TEXT_METRICS()
    GetMetrics = call_com(text_layout, 60, c_long, POINTER(DWRITE_TEXT_METRICS))
    check_hr(GetMetrics(text_layout, byref(t_metrics)), "GetMetrics")
    
    width = int(math.ceil(t_metrics.width)) + 10
    height = int(math.ceil(t_metrics.height))
    if width <= 0: width = 1
    if height <= 0: height = 1
    
    if _cached_bitmap is None or width > _cached_width or height > _cached_height:
        if _cached_brush: call_com(_cached_brush, 2, c_ulong)(_cached_brush)
        if _cached_sel_brush: call_com(_cached_sel_brush, 2, c_ulong)(_cached_sel_brush)
        if _cached_caret_brush: call_com(_cached_caret_brush, 2, c_ulong)(_cached_caret_brush)
        if _cached_rt: call_com(_cached_rt, 2, c_ulong)(_cached_rt)
        if _cached_bitmap: call_com(_cached_bitmap, 2, c_ulong)(_cached_bitmap)
        _cached_brush = None
        _cached_sel_brush = None
        _cached_caret_brush = None
        _cached_rt = None
        _cached_bitmap = None
        
        alloc_w = max(width, 2048)
        alloc_h = max(height, 256)
        
        bitmap = c_void_p()
        CreateBitmap = call_com(_wic_factory, 17, c_long, c_uint, c_uint, POINTER(GUID), c_int, POINTER(c_void_p))
        check_hr(CreateBitmap(_wic_factory, c_uint(alloc_w), c_uint(alloc_h), byref(GUID_WICPixelFormat32bppPBGRA), c_int(1), byref(bitmap)), "CreateBitmap")
        _cached_bitmap = bitmap
        
        render_target = c_void_p()
        props = D2D1_RENDER_TARGET_PROPERTIES(0, D2D1_PIXEL_FORMAT(0, 0), 0.0, 0.0, 0, 0)
        CreateWicBitmapRenderTarget = call_com(_d2d_factory, 13, c_long, c_void_p, POINTER(D2D1_RENDER_TARGET_PROPERTIES), POINTER(c_void_p))
        check_hr(CreateWicBitmapRenderTarget(_d2d_factory, _cached_bitmap, byref(props), byref(render_target)), "CreateWicBitmapRenderTarget")
        _cached_rt = render_target
        
        brush = c_void_p()
        sel_brush = c_void_p()
        caret_brush = c_void_p()
        CreateSolidColorBrush = call_com(render_target, 8, c_long, POINTER(D2D1_COLOR_F), c_void_p, POINTER(c_void_p))
        CreateSolidColorBrush(render_target, byref(D2D1_COLOR_F(0, 0, 0, 1.0)), None, byref(brush))
        CreateSolidColorBrush(render_target, byref(D2D1_COLOR_F(0, 0, 0, 1.0)), None, byref(sel_brush))
        CreateSolidColorBrush(render_target, byref(D2D1_COLOR_F(0, 0, 0, 1.0)), None, byref(caret_brush))
        _cached_brush = brush
        _cached_sel_brush = sel_brush
        _cached_caret_brush = caret_brush
        
        _cached_width = alloc_w
        _cached_height = alloc_h

    bitmap = _cached_bitmap
    render_target = _cached_rt
    brush = _cached_brush
    sel_brush = _cached_sel_brush
    caret_brush = _cached_caret_brush

    # Update brush colors (ID2D1SolidColorBrush::SetColor is index 8)
    SetColor = call_com(brush, 8, None, POINTER(D2D1_COLOR_F))
    SetColor(brush, byref(D2D1_COLOR_F(fg_rgb[0]/255.0, fg_rgb[1]/255.0, fg_rgb[2]/255.0, 1.0)))
    SetColor = call_com(sel_brush, 8, None, POINTER(D2D1_COLOR_F))
    SetColor(sel_brush, byref(D2D1_COLOR_F(sel_rgb[0]/255.0, sel_rgb[1]/255.0, sel_rgb[2]/255.0, 1.0)))
    SetColor = call_com(caret_brush, 8, None, POINTER(D2D1_COLOR_F))
    SetColor(caret_brush, byref(D2D1_COLOR_F(caret_rgb[0]/255.0, caret_rgb[1]/255.0, caret_rgb[2]/255.0, 1.0)))
    
    call_com(render_target, 48, None)(render_target) # BeginDraw
    call_com(render_target, 47, None, POINTER(D2D1_COLOR_F))(render_target, byref(D2D1_COLOR_F(bg_rgb[0]/255.0, bg_rgb[1]/255.0, bg_rgb[2]/255.0, 1.0))) # Clear
    
    FillRectangle = call_com(render_target, 17, None, POINTER(D2D1_RECT_F), c_void_p)
    
    if selection is not None:
        s_start, s_end = selection
        for i in range(s_start, s_end):
            if HitTestTextPosition(text_layout, i, 0, byref(px), byref(py), byref(metrics)) == 0:
                rect = D2D1_RECT_F(metrics.left, 0.0, metrics.left + metrics.width, float(height))
                FillRectangle(render_target, byref(rect), sel_brush)
                
    DrawTextLayout = call_com(render_target, 28, None, D2D1_POINT_2F, c_void_p, c_void_p, c_uint)
    DrawTextLayout(render_target, D2D1_POINT_2F(0.0, 0.0), text_layout, brush, 4)
    
    if cursor_pos is not None:
        is_trailing = 0
        if cursor_pos > 0 and cursor_pos == len(text):
            cursor_pos -= 1
            is_trailing = 1
        if HitTestTextPosition(text_layout, cursor_pos, is_trailing, byref(px), byref(py), byref(metrics)) == 0:
            cx = px.value
            rect = D2D1_RECT_F(cx, 0.0, cx + 2.0, float(height))
            FillRectangle(render_target, byref(rect), caret_brush)
    
    call_com(render_target, 49, c_long, POINTER(c_ulonglong), POINTER(c_ulonglong))(render_target, None, None) # EndDraw
    
    wic_lock = c_void_p()
    rect = WICRect(0, 0, width, height)
    Lock = call_com(bitmap, 8, c_long, POINTER(WICRect), c_uint, POINTER(c_void_p))
    check_hr(Lock(bitmap, byref(rect), 2, byref(wic_lock)), "Lock")
    
    stride_c = c_uint()
    GetStride = call_com(wic_lock, 4, c_long, POINTER(c_uint))
    check_hr(GetStride(wic_lock, byref(stride_c)), "GetStride")
    stride = stride_c.value
    
    size = c_uint()
    data_ptr = c_void_p()
    GetDataPointer = call_com(wic_lock, 5, c_long, POINTER(c_uint), POINTER(c_void_p))
    check_hr(GetDataPointer(wic_lock, byref(size), byref(data_ptr)), "GetDataPointer")
    
    read_size = (height - 1) * stride + width * 4
    buf = (c_ubyte * read_size).from_address(data_ptr.value)
    
    pixels = bytearray(width * height * 4)
    if stride == width * 4:
        pixels[:] = buf[:width * height * 4]
    else:
        for y in range(height):
            src_offset = y * stride
            dst_offset = y * width * 4
            pixels[dst_offset:dst_offset + width * 4] = buf[src_offset:src_offset + width * 4]
            
    call_com(wic_lock, 2, c_ulong)(wic_lock)
    call_com(text_layout, 2, c_ulong)(text_layout)
    
    png = _rgba_to_png(width, height, pixels)
    b64 = base64.b64encode(png).decode('ascii')
    return "data:image/png;base64,%s" % b64

if __name__ == "__main__":
    uri = get_line_image_d2d("Hello 🍎 World!", "Consolas", 14, (100, 200, 100), (30, 30, 30), (255, 255, 255), (80, 80, 80), cursor_pos=7, selection=(0, 5))
    print(uri[:50] + "...")
