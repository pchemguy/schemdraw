''' SVG drawing backend for schemdraw '''

from __future__ import annotations
from typing import Literal, Sequence, Optional
from xml.etree import ElementTree as ET

import os
import sys
import subprocess
import tempfile
import math

try:
    import ziamath
except ImportError:
    ziamath = None  # type: ignore

from ..types import Capstyle, Joinstyle, Linestyle, BBox
from ..util import Point
from . import svgtext

TextModeType = Literal['path', 'text']

textmode: TextModeType = 'path' if ziamath is not None else 'text'


def settextmode(mode: TextModeType) -> None:
    ''' Set the mode for rendering text in the SVG backend.

        In 'text' mode, text is drawn as SVG <text> elements
        and will be searchable in the SVG, however it may
        render differently on systems without the same fonts
        installed. In 'path' mode, text converted to SVG
        <path> elements and will render independently of
        any fonts on the system. Path mode enables full
        rendering of math expressions, but also requires the
        ziafont/ziamath packages.

        Args:
            mode: Text Mode.
    '''
    if mode == 'path' and ziamath is None:
        raise ValueError('Path mode requires ziamath package')
    global textmode
    textmode = mode


def isnotebook():
    ''' Determine whether code is running in Jupyter/interactive mode '''
    try:
        shell = get_ipython().__class__.__name__
        return shell in ['ZMQInteractiveShell', 'SpyderShell']
    except NameError:
        return False


inline = isnotebook()


def getstyle(color: str=None, ls: Linestyle=None, lw: float=None,
             capstyle: Capstyle=None, joinstyle: Joinstyle=None,
             fill: str=None) -> str:
    ''' Get style for svg element. Leave empty if property matches default '''
    # Note: styles are added to every SVG element, rather than in a global <style>
    # tag, since multiple images in one HTML page may share <styles>.
    s = ''
    if color:
        s += f'stroke:{color};'
    s += f'fill:{str(fill).lower()};'
    if lw:
        s += f'stroke-width:{lw};'
    dash = {'--': '7.4,3.2',
            'dashed': '7.4,3.2',
            ':': '2,3.3',
            'dotted': '2,3.3',
            '-.': '12.8,3.2,2,3.2',
            'dashdot': '12.8,3.2,2,3.2',
            }.get(ls, None)  # type: ignore
    if dash is not None:
        s += f'stroke-dasharray:{dash};'
    if capstyle:
        if capstyle == 'projecting':  # Matplotlib notation
            capstyle = 'square'
        s += f'stroke-linecap:{capstyle};'
    if joinstyle:
        s += f'stroke-linejoin:{joinstyle};'

    return s


def text_size(text: str, font: str='Arial', size: float=16) -> tuple[float, float, float]:
    ''' Get size of text. Size will be exact bounding box if ziamath installed and
        using path text mode. Otherwise size will be estimated based on character
        widths.

        Args:
            text: string to calculate
            font: Font family
            size: Font size in points
        
        Returns:
            width, height, linespacing
    '''
    if ziamath:
        if text == '':
            return 0, 0, 0

        if font.lower() in ['sans-serif', 'Arial']:
            font = 'sans'
        
        lines = text.splitlines()
        maths = []
        for line in lines:
            maths.append(ziamath.Math.fromlatextext(line, size=size, mathstyle=font, textstyle=font))
        sizes = [m.getsize() for m in maths]
        w: float = max([s[0] for s in sizes])
        h: float = sum([s[1] for s in sizes])
        textsize = (w, h, 0.)
    else:
        textsize = svgtext.text_approx_size(text, font=font, size=size)
    return textsize


class Figure:
    ''' Schemdraw figure drawn directly to SVG

        Args:
            bbox: Coordinate bounding box for drawing
            inches_per_unit: Scale for the drawing
            showframe: Show frame around entire drawing
    '''
    def __init__(self, bbox: BBox, **kwargs):
        self.svgelements: list[tuple[int, ET.Element]] = []  # (zorder, element)
        self.showframe = kwargs.get('showframe', False)
        self.scale = 64.8 * kwargs.get('inches_per_unit', .5)  # Magic scale factor that matches what MPL did
        self.set_bbox(bbox)
        self._bgcolor: Optional[str] = None

    def set_bbox(self, bbox: BBox) -> None:
        ''' Set the bounding box '''
        self.bbox = bbox
        self.pxwidth = (self.bbox.xmax-self.bbox.xmin) * self.scale
        self.pxheight = (self.bbox.ymax-self.bbox.ymin) * self.scale
        self.pxwidth = max(5, self.pxwidth)
        self.pxheight = max(5, self.pxheight)

    def xform(self, x: float, y: float) -> tuple[float, float]:
        ''' Convert x, y in user coords to svg pixel coords '''
        return x*self.scale, -y*self.scale

    def bgcolor(self, color: str) -> None:
        ''' Set background color of drawing '''
        self._bgcolor = color

    def plot(self, x: Sequence[float], y: Sequence[float],
             color: str='black', ls: Linestyle='-', lw: float=2,
             fill: str='none', capstyle: Capstyle='round',
             joinstyle: Joinstyle='round', zorder: int=2) -> None:
        ''' Plot a path '''
        et = ET.Element('path')
        d = 'M {},{} '.format(*self.xform(x[0], y[0]))
        for xx, yy in zip(x[1:], y[1:]):
            if str(xx) == 'nan' or str(yy) == 'nan':
                d += 'M '
                continue
            elif not d.endswith('M '):
                d += 'L '
            xx, yy = self.xform(xx, yy)
            d += '{},{} '.format(xx, yy)

        d = d.strip()
        et.set('d', d)
        et.set('style', getstyle(color=color, ls=ls, lw=lw, capstyle=capstyle,
                                 joinstyle=joinstyle, fill=fill))
        self.svgelements.append((zorder, et))

    def text(self, s: str, x: float, y: float, color: str='black',
             fontsize: float=14, fontfamily: str='sans-serif',
             rotation: float=0, halign: Literal['left', 'center', 'right']='center',
             valign: Literal['top', 'center', 'bottom']='center',
             rotation_mode: Literal['anchor', 'default']='anchor', zorder: int=3) -> None:
        ''' Add text to the figure '''
        if s == '':
            return

        x, y = self.xform(x, y)

        # TODO: Add user setting for text vs path. Enable path only if ziamath installed.
        if ziamath and textmode == 'path':
            texttag = ET.Element('g')
            if fontfamily.lower() in ['sans-serif', 'Arial']:
                fontfamily = 'sans'

            lines = s.splitlines()
            maths = []
            for i, line in enumerate(lines):
                maths.append(ziamath.Math.fromlatextext(
                    line, size=fontsize, mathstyle=fontfamily, textstyle=fontfamily))

            dys = [max(m.node.bbox.ymax*1.25, 0) for m in maths]
            if len(lines) > 1:
                topbase = sum(dys[:-1])
                if valign == 'bottom':
                    y -= topbase
                elif valign == 'center':
                    y -= topbase/2 - fontsize/2
                elif valign == 'top':
                    y += topbase
            elif valign == 'top':
                y += dys[0]
            elif valign == 'center':
                y += dys[0]/2

            yi = y
            for dy, m in zip(dys, maths):
                m.drawon(x, yi, texttag, halign=halign, valign='baseline', color=color)
                yi += dy

            if rotation:
                texttag.set('transform', f'rotate({-rotation} {x} {y})')

        else:
            texttag = svgtext.text_tosvg(s, x, y, font=fontfamily, size=fontsize,
                                         halign=halign, valign=valign, color=color,
                                         rotation=rotation, rotation_mode=rotation_mode,
                                         testmode=False)

        self.svgelements.append((zorder, texttag))

    def poly(self, verts: Sequence[Sequence[float]], closed: bool=True,
             color: str='black', fill: str='none', lw: float=2,
             ls: Linestyle='-', capstyle: Capstyle='round',
             joinstyle: Joinstyle='round', zorder: int=1) -> None:
        ''' Draw a polygon '''
        if not closed:
            x = [v[0] for v in verts]
            y = [v[1] for v in verts]
            self.plot(x, y, color=color, fill=fill, lw=lw, ls=ls,
                      capstyle=capstyle, joinstyle=joinstyle, zorder=zorder)
        else:
            et = ET.Element('polygon')
            points = ''
            for xx, yy in verts:
                xx, yy = self.xform(xx, yy)
                points += '{},{} '.format(xx, yy)
            et.set('points', points)
            et.set('style', getstyle(color=color, ls=ls, lw=lw, capstyle=capstyle,
                                     joinstyle=joinstyle, fill=fill))
            self.svgelements.append((zorder, et))

    def circle(self, center: Sequence[float], radius: float, color: str='black',
               fill: str='none', lw: float=2, ls: Linestyle='-', zorder: int=1) -> None:
        ''' Draw a circle '''
        x, y = self.xform(*center)
        radius = radius * self.scale
        et = ET.Element('circle')
        et.set('cx', str(x))
        et.set('cy', str(y))
        et.set('r', str(radius))
        et.set('style', getstyle(color=color, lw=lw, ls=ls, fill=fill))
        self.svgelements.append((zorder, et))

    def arrow(self, x: float, y: float, dx: float, dy: float,
              headwidth: float=.2, headlength: float=.2,
              color: str='black', lw: float=2, zorder: int=1) -> None:
        ''' Draw an arrow '''
        x, y = self.xform(x, y)
        dx, dy = dx*self.scale, dy*self.scale
        headwidth = headwidth*self.scale
        headlength = headlength*self.scale

        # Draw arrow as path
        head = Point((x+dx, y-dy))
        tail = Point((x, y))
        fullen = math.sqrt(dx**2 + dy**2)
        theta = -math.degrees(math.atan2(dy, dx))

        finc = Point((fullen - headlength, 0)).rotate(theta) + tail
        fin1 = Point((fullen - headlength, headwidth/2)).rotate(theta) + tail
        fin2 = Point((fullen - headlength, -headwidth/2)).rotate(theta) + tail

        # Shrink arrow head by lw so it points right at the line
        head = Point((head[0] - lw * math.cos(math.radians(theta)),
                      head[1] - lw * math.sin(math.radians(theta))))
        
        et1 = ET.Element('path')
        d = f'M {head[0]} {head[1]} '
        d += f'L {fin1[0]} {fin1[1]} '
        d += f'L {fin2[0]} {fin2[1]} Z'
        et1.set('d', d)
        et1.set('style', getstyle(color=color, lw=0, capstyle='butt',
                                  joinstyle='miter', fill=color))

        et2 = ET.Element('path')
        d = f'M {finc[0]} {finc[1]} '
        d += f'L {tail[0]} {tail[1]} Z'
        et2.set('d', d)
        et2.set('style', getstyle(color=color, lw=lw, capstyle='butt',
                                  joinstyle='miter', fill=color))
        self.svgelements.append((zorder, et1))
        self.svgelements.append((zorder, et2))

    def arc(self, center: Sequence[float], width: float, height: float,
            theta1: float=0, theta2: float=90, angle: float=0,
            color: str='black', lw: float=2, ls: Linestyle='-', zorder: int=1,
            arrow: bool=None) -> None:
        ''' Draw an arc or ellipse, with optional arrowhead '''
        centerx, centery = self.xform(*center)
        width, height = width*self.scale, height*self.scale
        angle = -angle
        theta1 = -theta1
        theta2 = -theta2
        anglerad = math.radians(angle)

        theta1 = math.radians(theta1)
        theta2 = math.radians(theta2)
        t1 = math.atan2(width*math.sin(theta1), height*math.cos(theta1))
        t2 = math.atan2(width*math.sin(theta2), height*math.cos(theta2))
        while t1 < t2:
            t1 += 2*math.pi

        startx = (centerx + width/2 * math.cos(t2)*math.cos(anglerad) 
                  - height/2 * math.sin(t2)*math.sin(anglerad))
        starty = (centery + width/2 * math.cos(t2)*math.sin(anglerad) 
                  + height/2 * math.sin(t2)*math.cos(anglerad))
        endx = (centerx + width/2 * math.cos(t1)*math.cos(anglerad) 
                - height/2 * math.sin(t1)*math.sin(anglerad))
        endy = (centery + width/2 * math.cos(t1)*math.sin(anglerad) 
                + height/2 * math.sin(t1)*math.cos(anglerad))

        startx, starty = round(startx, 2), round(starty, 2)
        endx, endy = round(endx, 2), round(endy, 2)
        dx, dy = endx-startx, endy-starty

        if abs(dx) < .1 and abs(dy) < .1:
            # Full ellipse - can't be drawn with a single <path>
            # because when start/end points are the same it draws a dot.
            et = ET.Element('ellipse')
            et.set('cx', str(centerx))
            et.set('cy', str(centery))
            et.set('rx', str(width/2))
            et.set('ry', str(height/2))
            if angle != 0:
                et.set('transform', f'rotate({angle} {centerx} {centery})')
            et.set('stroke', color)
            et.set('stroke-width', str(lw))
            et.set('fill', 'none')
            self.svgelements.append((zorder, et))

        else:
            flags = '1 1' if abs(t2-t1) >= math.pi else '0 1'
            et = ET.Element('path')
            d = f'M {startx} {starty}'
            d += f' a {width/2} {height/2} {angle} {flags} {dx} {dy}'
            et.set('d', d)
            et.set('stroke', color)
            et.set('stroke-width', str(lw))
            et.set('fill', 'none')
            self.svgelements.append((zorder, et))

        if arrow is not None:
            # Back to user coordinates
            width, height = width/self.scale, height/self.scale
            angle = -angle
            theta1 = -math.degrees(theta1)
            theta2 = -math.degrees(theta2)

            headlength = .25
            headwidth = .15

            x, y = math.cos(math.radians(theta2)), math.sin(math.radians(theta2))
            th2 = math.degrees(math.atan2((width/height)*y, x))
            x, y = math.cos(math.radians(theta1)), math.sin(math.radians(theta1))
            th1 = math.degrees(math.atan2((width/height)*y, x))
            if arrow == 'ccw':
                dx = math.cos(math.radians(th2+90)) * headlength
                dy = math.sin(math.radians(theta2+90)) * headlength
                xy = Point((center[0] + width/2*math.cos(math.radians(th2)),
                            center[1] + height/2*math.sin(math.radians(th2))))
            else:
                dx = -math.cos(math.radians(th1+90)) * headlength
                dy = -math.sin(math.radians(th1+90)) * headlength
                xy = Point((center[0] + width/2*math.cos(math.radians(th1)),
                            center[1] + height/2*math.sin(math.radians(th1))))

            xy = Point(xy).rotate(angle, center)
            darrow = Point((dx, dy)).rotate(angle)

            self.arrow(xy[0], xy[1], darrow[0], darrow[1], headwidth=headwidth,
                       headlength=headlength, color=color, lw=1, zorder=zorder)

    def save(self, fname: str, **kwargs) -> None:
        ''' Save the figure to a file '''
        svg = self.getimage().decode()
        with open(fname, 'w') as f:
            f.write(svg)

    def getimage(self, ext: str='svg') -> bytes:
        ''' Get the image as SVG or PNG bytes array '''
        pad = 2
        x0 = self.bbox.xmin * self.scale - pad
        y0 = -self.bbox.ymax * self.scale - pad
        svg = ET.Element('svg')
        svg.set('xmlns', 'http://www.w3.org/2000/svg')
        svg.set('xml:lang', 'en')
        svg.set('height', f'{self.pxheight+2*pad}pt')
        svg.set('width', f'{self.pxwidth+2*pad}pt')
        svg.set('viewBox', f'{x0} {y0} {self.pxwidth+2*pad} {self.pxheight+2*pad}')
        if self._bgcolor:
            svg.set('style', f'background-color:{self._bgcolor};')

        if self.showframe:
            rect = ET.SubElement(svg, 'rect')
            rect.set('x', str(x0))
            rect.set('y', str(y0))
            rect.set('width', str(self.pxwidth))
            rect.set('height', str(self.pxheight))
            rect.set('style', 'fill:none; stroke-width:1; stroke:black;')

        # sort by zorder
        elements = [k[1] for k in sorted(self.svgelements, key=lambda x: x[0])]
        for elm in elements:
            svg.append(elm)
        return ET.tostring(svg, encoding='utf-8')

    def clear(self) -> None:
        ''' Remove everything '''
        self.svgelements = []

    def _repr_svg_(self):
        ''' SVG representation for Jupyter '''
        return self.getimage().decode()

    def __repr__(self):
        return self.getimage().decode()

    def show(self) -> None:
        ''' Show drawing in default program, if not inline in Jupyter '''
        if not inline:
            handle, path = tempfile.mkstemp(suffix='.svg')
            with os.fdopen(handle, 'w') as f:
                f.write(self.getimage().decode())

            if sys.platform == 'win32':
                os.startfile(path)
            else:
                cmd = 'open' if sys.platform == 'darwin' else 'xdg-open'
                try:
                    subprocess.call([cmd, path])
                except FileNotFoundError:  # Some linux without display may not have xdg-open
                    pass