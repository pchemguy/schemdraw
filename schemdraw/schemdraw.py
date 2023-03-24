''' Schemdraw Drawing class '''

from __future__ import annotations
from typing import Type, Any, MutableMapping, Union, Optional, TYPE_CHECKING
from collections import ChainMap
import warnings
import math

from . import default_canvas
from .types import BBox, Backends, ImageFormat, Linestyle, XY, ImageType
from .elements import Element
from .segments import SegmentType
from .util import Point
from .backends.svg import Figure as svgFigure

if TYPE_CHECKING:
    import xml.etree.ElementTree.Element  # type: ignore

try:
    from .backends.mpl import Figure as mplFigure
    default_canvas.default_canvas = 'matplotlib'
    if TYPE_CHECKING:
        import matplotlib.pyplot.Axes   # type: ignore
except ImportError:
    mplFigure = None  # type: ignore
    default_canvas.default_canvas = 'svg'


def use(backend: Backends = 'matplotlib') -> None:
    ''' Change default backend, either 'matplotlib' or 'svg' '''
    if backend == 'matplotlib':
        if mplFigure is None:
            raise ValueError('Could not import Matplotlib.')
    default_canvas.default_canvas = backend


def config(unit: float = 3.0, inches_per_unit: float = 0.5,
           lblofst: float = 0.1, fontsize: float = 14.,
           font: str = 'sans-serif', color: str = 'black',
           lw: float = 2., ls: Linestyle = '-',
           fill: str = None, bgcolor: str = None) -> None:
    ''' Set global schemdraw style configuration

        Args:
            unit: Full length of a 2-terminal element. Inner zig-zag portion
                of a resistor is 1.0 units.
            inches_per_unit: Inches per drawing unit for setting drawing scale
            lblofst: Default offset between element and its label
            fontsize: Default font size for text labels
            font: Default font family for text labels
            color: Default color name or RGB (0-1) tuple
            lw: Default line width for elements
            ls: Default line style
            fill: Deault fill color for closed elements
    '''
    schemdrawstyle['unit'] = unit
    schemdrawstyle['inches_per_unit'] = inches_per_unit
    schemdrawstyle['lblofst'] = lblofst
    schemdrawstyle['fontsize'] = fontsize
    schemdrawstyle['font'] = font
    schemdrawstyle['color'] = color
    schemdrawstyle['lw'] = lw
    schemdrawstyle['ls'] = ls
    schemdrawstyle['fill'] = fill
    if bgcolor:
        schemdrawstyle['bgcolor'] = bgcolor


schemdrawstyle: dict[str, Any] = {}  # Global style
config()  # Initialize default configuration


def theme(theme='default'):
    ''' Set schemdraw theme (line color and background color).
        Themes match those in jupyter-themes package
        (https://github.com/dunovank/jupyter-themes).

        Available themes:
            * default (black on white)
            * dark (white on black)
            * solarizedd
            * solarizedl
            * onedork
            * oceans16
            * monokai
            * gruvboxl
            * gruvboxd
            * grade3
            * chesterish
    '''
    if theme == 'default':
        config(bgcolor='white')
    elif theme == 'dark':
        schemdrawstyle['color'] = 'white'
        schemdrawstyle['bgcolor'] = 'black'
    elif theme == 'solarizedd':
        schemdrawstyle['bgcolor'] = '#002b36'
        schemdrawstyle['color'] = '#657b83'
    elif theme == 'solarizedl':
        schemdrawstyle['bgcolor'] = '#eee8d5'
        schemdrawstyle['color'] = '#073642'
    elif theme == 'onedork':
        schemdrawstyle['bgcolor'] = '#373e4b'
        schemdrawstyle['color'] = '#899ab8'
    elif theme == 'oceans16':
        schemdrawstyle['bgcolor'] = '#384151'
        schemdrawstyle['color'] = '#CDD2E9'
    elif theme == 'monokai':
        schemdrawstyle['bgcolor'] = '#232323'
        schemdrawstyle['color'] = '#BBBBBB'
    elif theme == 'gruvboxl':
        schemdrawstyle['bgcolor'] = '#ebdbb2'
        schemdrawstyle['color'] = '#3c3836'
    elif theme == 'gruvboxd':
        schemdrawstyle['bgcolor'] = '#1d2021'
        schemdrawstyle['color'] = '#d5c4a1'
    elif theme == 'grade3':
        schemdrawstyle['bgcolor'] = '#ffffff'
        schemdrawstyle['color'] = '#3f3d46'
    elif theme == 'chesterish':
        schemdrawstyle['bgcolor'] = '#323A48'
        schemdrawstyle['color'] = '#92A2BD'
    else:
        raise ValueError(f'Unknown theme {theme}')


class Drawing:
    ''' A schematic drawing

        See `schemdraw.config` method for argument defaults

        Args:
            canvas: Canvas to draw on when using Drawing context manager.
                Can be string 'matplotlib' or 'svg' to create new canvas
                with these backends, or an instance of a matplotlib axis,
                or an instance of xml.etree.ElementTree containing SVG.
                Default is value set by schemdraw.use().
            file: optional filename to save on exiting context manager
                or calling draw method.
            show: Show the drawing after exiting context manager

        Attributes:
            here: (xy tuple) Current drawing position. The next element will
                be added at this position unless specified otherwise.
            theta: (float) Current drawing angle, in degrees. The next
                element will be added with this angle unless specified
                otherwise.
    '''
    def __init__(self, canvas: Union[Backends, xml.etree.ElementTree.Element,
                                     matplotlib.pyplot.Axes] = None,
                 file: str = None, show: bool = True, **kwargs):
        self.outfile = file
        self.canvas = canvas
        self.show = show
        self.elements: list[Element] = []
        self.anchors: MutableMapping[str, Union[Point, tuple[float, float]]] = {}  # Untransformed anchors

        if 'backend' in kwargs:
            self.canvas = kwargs.pop('backend')
            warnings.warn('Use of `backend` is deprecated. Use `canvas`.',
                          DeprecationWarning, stacklevel=2)

        self.dwgparams: dict[str, Any] = schemdrawstyle.copy()
        self.dwgparams.update(kwargs)  # To maintain support for arguments that moved to config method
        self.unit = kwargs.get('unit', schemdrawstyle.get('unit'))

        self.here: XY = Point((0, 0))
        self.theta: float = 0
        self._state: list[tuple[Point, float]] = []  # Push/Pop stack
        self._interactive = False
        self.fig: Optional[Union[mplFigure, svgFigure]] = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        ''' Exit context manager - save to file and display '''
        if self.outfile is not None:
            self.save(self.outfile)
        dwg = self.draw(show=False)
        if self.show and not hasattr(self.canvas, 'plot'):
            try:
                display(dwg)
            except NameError:  # Not in Jupyter/IPython
                pass

    def __getattr__(self, name: str) -> Any:
        ''' Allow getting anchor position as attribute '''
        if name in vars(self).get('anchors', {}):
            return vars(self).get('anchors')[name]  # type: ignore
        raise AttributeError(f"'Drawing' has no attribute {name}")

    def interactive(self, interactive: bool = True):
        ''' Enable interactive mode (matplotlib backend only). Matplotlib
            must also be set to interactive with `plt.ion()`.
        '''
        self._interactive = interactive

    def get_bbox(self) -> BBox:
        ''' Get drawing bounding box '''
        xmin = math.inf
        xmax = -math.inf
        ymin = math.inf
        ymax = -math.inf
        for element in self.elements:
            bbox = element.get_bbox(transform=True)
            xmin = min(bbox.xmin, xmin)
            xmax = max(bbox.xmax, xmax)
            ymin = min(bbox.ymin, ymin)
            ymax = max(bbox.ymax, ymax)
        return BBox(xmin, ymin, xmax, ymax)

    def get_segments(self) -> list[SegmentType]:
        ''' Get flattened list of all segments in the drawing '''
        segments = []
        for element in self.elements:
            params = ChainMap(element._userparams, element.params)
            segments.extend([s.xform(element.transform, **params)
                             for s in element.segments])
        return segments

    def _repr_svg_(self):
        ''' SVG representation for Jupyter '''
        return self.draw(show=False).getimage('svg').decode()

    def _repr_png_(self):
        ''' PNG representation for Jupyter '''
        if self.canvas == 'matplotlib' or hasattr(self.canvas, 'plot'):
            return self.draw(show=False).getimage('png')
        return None

    def __iadd__(self, element: Element):
        ''' In-place add element, via += operator. '''
        self.add(element)
        return self

    def add(self, element: Element) -> Element:
        ''' Add an element to the drawing.

            Args:
                element: The element to add.
        '''
        self.here, self.theta = element._place(self.here, self.theta, **self.dwgparams)
        self.elements.append(element)

        if self._interactive:
            if self.fig is None:
                self.fig = mplFigure(
                    bbox=self.get_bbox(),
                    inches_per_unit=self.dwgparams.get('inches_per_unit'))
                if 'bgcolor' in self.dwgparams:
                    self.fig.bgcolor(self.dwgparams['bgcolor'])
            element._draw(self.fig)
            self.fig.set_bbox(self.get_bbox())  # type: ignore
            self.fig.getimage()  # type: ignore
        else:
            self.fig = None  # Clear any existing figure
        return element

    def add_elements(self, *elements: Element) -> None:
        ''' Add multiple elements to the drawing '''
        for element in elements:
            self.add(element)

    def undo(self) -> None:
        ''' Removes previously added element '''
        self.elements.pop(-1)
        self.fig.clear()  # type: ignore
        for element in self.elements:
            element._draw(self.fig)
        self.here, self.theta = self.elements[-1].absdrop
        self.fig.set_bbox(self.get_bbox())  # type: ignore
        self.fig.getimage()  # type: ignore

    def move(self, dx: float = 0, dy: float = 0) -> None:
        ''' Move the current drawing position

            Args:
                dx: change in x position
                dy: change in y position
        '''
        self.here = Point((self.here[0] + dx, self.here[1] + dy))

    def move_from(self, ref: Point, dx: float = 0, dy: float = 0, theta: float = None) -> None:
        ''' Move drawing position relative to the reference point. Change drawing
            theta if provided.
        '''
        self.here = (ref.x + dx, ref.y + dy)
        if theta is not None:
            self.theta = theta

    def set_anchor(self, name: str) -> None:
        ''' Define a Drawing anchor at the current drawing position '''
        self.anchors[name] = self.here

    def push(self) -> None:
        ''' Push/save the drawing state.
            Drawing.here and Drawing.theta are saved.
        '''
        self._state.append((Point(self.here), self.theta))

    def pop(self) -> None:
        ''' Pop/load the drawing state. Location and angle are returned to
            previously pushed state.
        '''
        if len(self._state) > 0:
            self.here, self.theta = self._state.pop()

    def config(self, unit: float = None, inches_per_unit: float = None,
               fontsize: float = None, font: str = None,
               color: str = None, lw: float = None, ls: Linestyle = None,
               fill: str = None, bgcolor: str = None) -> None:
        ''' Set Drawing configuration, overriding schemdraw global config.

            Args:
                unit: Full length of a 2-terminal element. Inner zig-zag portion
                    of a resistor is 1.0 units.
                inches_per_unit: Inches per drawing unit for setting drawing scale
                fontsize: Default font size for text labels
                font: Default font family for text labels
                color: Default color name or RGB (0-1) tuple
                lw: Default line width for elements
                ls: Default line style
                fill: Deault fill color for closed elements
        '''
        if unit is not None:
            self.unit = unit
            self.dwgparams['unit'] = unit
        if inches_per_unit is not None:
            self.dwgparams['inches_per_unit'] = inches_per_unit
        if font is not None:
            self.dwgparams['font'] = font
        if fontsize is not None:
            self.dwgparams['fontsize'] = fontsize
        if color is not None:
            self.dwgparams['color'] = color
        if lw is not None:
            self.dwgparams['lw'] = lw
        if ls is not None:
            self.dwgparams['ls'] = ls
        if fill is not None:
            self.dwgparams['fill'] = fill
        if bgcolor is not None:
            self.dwgparams['bgcolor'] = bgcolor

    def _drawelements(self):
        ''' Draw all the elements on self.fig '''
        for element in self.elements:
            element._draw(self.fig)

    def _drawmpl(self, ax=None, showframe=False):
        ''' Draw on Matplotlib Axis '''
        if self.fig is None or ax is not None:
            self.fig = mplFigure(ax=ax,
                                 bbox=self.get_bbox(),
                                 inches_per_unit=self.dwgparams.get('inches_per_unit'),
                                 showframe=showframe)
            if 'bgcolor' in self.dwgparams:
                self.fig.bgcolor(self.dwgparams['bgcolor'])
        self._drawelements()

    def _drawsvg(self, svg=None, showframe=False):
        ''' Draw on SVG canvas '''
        if self.fig is None or svg is not None:
            self.fig = svgFigure(svg=svg, bbox=self.get_bbox(),
                                 inches_per_unit=self.dwgparams.get('inches_per_unit'),
                                 showframe=showframe)
        if 'bgcolor' in self.dwgparams:
            self.fig.bgcolor(self.dwgparams['bgcolor'])
        self._drawelements()

    def draw(self, showframe: bool = False, show: bool = True,
             canvas=None, backend: Backends = None):
        ''' Draw the schematic

            Args:
                showframe: Show axis frame. Useful for debugging a drawing.
                show: Show the schematic in a GUI popup window (when
                    outside of a Jupyter inline environment)
                canvas: 'matplotlib', 'svg', or Axis instance to draw on
                backend (deprecated): 'matplotlib' or 'svg'

            Returns:
                schemdraw Figure object
        '''
        if backend:
            warnings.warn('Use of `backend` is deprecated. Use `canvas`.',
                          DeprecationWarning, stacklevel=2)
            canvas = backend

        if canvas is None:
            canvas = self.canvas
        if canvas is None:
            canvas = default_canvas.default_canvas

        if canvas == 'matplotlib':
            self._drawmpl(showframe=showframe)
        elif hasattr(canvas, 'plot'):
            self._drawmpl(ax=canvas, showframe=showframe)
        elif canvas == 'svg':
            self._drawsvg(showframe=showframe)
        else:
            self._drawsvg(canvas, showframe=showframe)

        if show:
            # Show figure in window if not inline/Jupyter mode
            self.fig.show()  # type: ignore

        if self.outfile is not None:
            self.save(self.outfile)

        return self.fig  # Return Figure and let _repr_ display it

    def save(self, fname: str, transparent: bool = True, dpi: float = 72) -> None:
        ''' Save figure to a file

            Args:
                fname: Filename to save. In Matplotlib backend, the file
                    type is automatically determined from extension
                    (png, svg, jpg). SVG backend only supports saving SVG format.
                transparent: Save as transparent background, if available
                dpi: Dots-per-inch for raster formats
        '''
        if self.fig is None:
            self.draw(show=False)
        self.fig.save(fname, transparent=transparent, dpi=dpi)  # type: ignore

    def get_imagedata(self, fmt: ImageFormat | ImageType = 'svg') -> bytes:
        ''' Get image data as bytes array

            Args:
                fmt: Format or file extension of the image type. SVG backend
                    only supports 'svg' format.

            Returns:
                Image data as bytes
        '''
        if self.canvas == 'svg' and fmt.lower() != 'svg':
            raise ValueError('Format not available in SVG backend.')
        if self.fig is None:
            self.draw(show=False)
        return self.fig.getimage(ext=fmt)  # type: ignore
