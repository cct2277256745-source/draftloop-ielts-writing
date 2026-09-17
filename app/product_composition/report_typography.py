"""Shared Songti / Times New Roman typography for learner and portfolio PDFs."""
from pathlib import Path
import os
import re
from xml.sax.saxutils import escape

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


def register_report_fonts():
    if 'ReportSongti' in pdfmetrics.getRegisteredFontNames(): return
    candidates=[(os.getenv('DRAFTLOOP_SONGTI_FONT',''),0,0),
        ('/System/Library/Fonts/Supplemental/Songti.ttc',6,1),
        ('C:/Windows/Fonts/simsun.ttc',0,0),('/Library/Fonts/SimSun.ttf',0,0)]
    for path,regular,bold in candidates:
        if not path or not Path(path).is_file(): continue
        try:
            pdfmetrics.registerFont(TTFont('ReportSongti',path,subfontIndex=regular))
            pdfmetrics.registerFont(TTFont('ReportSongtiBold',path,subfontIndex=bold))
            break
        except Exception: continue
    else: raise ValueError('PDF_SONGTI_FONT_REQUIRED')
    candidates=[(os.getenv('DRAFTLOOP_TIMES_FONT',''),os.getenv('DRAFTLOOP_TIMES_BOLD_FONT','')),
        ('/System/Library/Fonts/Supplemental/Times New Roman.ttf',
         '/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf'),
        ('C:/Windows/Fonts/times.ttf','C:/Windows/Fonts/timesbd.ttf')]
    for regular,bold in candidates:
        if not regular or not Path(regular).is_file(): continue
        pdfmetrics.registerFont(TTFont('ReportTimes',regular))
        pdfmetrics.registerFont(TTFont('ReportTimesBold',bold if Path(bold).is_file() else regular))
        break
    else: raise ValueError('PDF_TIMES_NEW_ROMAN_FONT_REQUIRED')
    for name in ('ReportSongti','ReportTimes'):
        pdfmetrics.registerFontFamily(name,normal=name,bold=name+'Bold',italic=name,boldItalic=name+'Bold')


def report_markup(value, *, bold=False):
    """Escape all supplied prose; only our font tags can enter ReportLab markup."""
    chunks=[]
    for piece in re.split(r'([\x00-\x7f]+)',str(value)):
        if not piece: continue
        family='ReportTimes' if piece.isascii() else 'ReportSongti'
        family+='Bold' if bold else ''
        chunks.append('<font name="'+family+'">'+escape(piece).replace('\n','<br/>')+'</font>')
    return ''.join(chunks)
