from enum import IntFlag

import comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 as __wrapper_module__
from comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 import (
    OLE_CANCELBOOL, GUID, OLE_YSIZE_HIMETRIC, _lcid, OLE_COLOR,
    OLE_YPOS_HIMETRIC, IPictureDisp, OLE_HANDLE, FONTITALIC, dispid,
    OLE_YSIZE_PIXELS, FONTNAME, Unchecked, FONTSTRIKETHROUGH,
    IFontEventsDisp, Gray, Default, CoClass, OLE_YPOS_PIXELS,
    EXCEPINFO, IFont, _check_version, IPicture, Monochrome, BSTR,
    DISPMETHOD, FONTUNDERSCORE, FONTBOLD, VgaColor,
    OLE_XPOS_CONTAINER, StdPicture, OLE_YPOS_CONTAINER, DISPPROPERTY,
    OLE_XSIZE_HIMETRIC, Picture, IUnknown, OLE_XSIZE_CONTAINER,
    OLE_YSIZE_CONTAINER, VARIANT_BOOL, FONTSIZE, Library, StdFont,
    OLE_ENABLEDEFAULTBOOL, COMMETHOD, DISPPARAMS, Font, HRESULT,
    OLE_XSIZE_PIXELS, OLE_XPOS_PIXELS, IFontDisp, typelib_path,
    IEnumVARIANT, Checked, Color, FontEvents, OLE_OPTEXCLUSIVE,
    OLE_XPOS_HIMETRIC, IDispatch
)


class LoadPictureConstants(IntFlag):
    Default = 0
    Monochrome = 1
    VgaColor = 2
    Color = 4


class OLE_TRISTATE(IntFlag):
    Unchecked = 0
    Checked = 1
    Gray = 2


__all__ = [
    'OLE_CANCELBOOL', 'FONTUNDERSCORE', 'OLE_YSIZE_HIMETRIC',
    'FONTBOLD', 'VgaColor', 'OLE_COLOR', 'OLE_YPOS_HIMETRIC',
    'IPictureDisp', 'OLE_XPOS_CONTAINER', 'StdPicture',
    'OLE_YPOS_CONTAINER', 'OLE_XSIZE_HIMETRIC', 'OLE_HANDLE',
    'Picture', 'FONTITALIC', 'OLE_XSIZE_CONTAINER',
    'OLE_YSIZE_CONTAINER', 'FONTSIZE', 'OLE_TRISTATE',
    'OLE_YSIZE_PIXELS', 'FONTNAME', 'Library', 'StdFont', 'Unchecked',
    'FONTSTRIKETHROUGH', 'IFontEventsDisp', 'Gray', 'Default',
    'OLE_ENABLEDEFAULTBOOL', 'OLE_YPOS_PIXELS', 'Font',
    'OLE_XSIZE_PIXELS', 'OLE_XPOS_PIXELS', 'IFontDisp',
    'LoadPictureConstants', 'typelib_path', 'Checked', 'Color',
    'IFont', 'FontEvents', 'OLE_XPOS_HIMETRIC', 'OLE_OPTEXCLUSIVE',
    'IPicture', 'Monochrome'
]

