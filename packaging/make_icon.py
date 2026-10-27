"""Draws Kinora's icon, the app's mark (an amber K on a rounded square, see .rail-mark in ui/index.html):
packaging/kinora.svg (Linux) and packaging/windows/kinora.ico (Windows), from the Krona One font in ui/fonts.

  uv run --with pillow --with fonttools --with brotli packaging/make_icon.py
"""
import io
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
LAMP, ON_LAMP = '#ffb45e', '#1a1006'
SIZE, RADIUS, GLYPH_HEIGHT = 256, 80, 116  # the box, its corners and the height of the K, in a 256 grid
ICO_SIZES = [ 16, 20, 24, 32, 40, 48, 64, 128, 256 ]

font = TTFont(ROOT / 'ui' / 'fonts' / 'krona-one-latin.woff2')
glyphs = font.getGlyphSet()
glyph = glyphs[font.getBestCmap()[ord('K')]]
bounds = BoundsPen(glyphs)
glyph.draw(bounds)
x0, y0, x1, y1 = bounds.bounds
scale = GLYPH_HEIGHT / (y1 - y0)
# font units (y up) to the 256 grid (y down), the K centred
offset_x = (SIZE - (x1 - x0) * scale) / 2 - x0 * scale
offset_y = (SIZE + (y1 - y0) * scale) / 2 + y0 * scale
path = SVGPathPen(glyphs, ntos = lambda value: '{:.2f}'.format(value).rstrip('0').rstrip('.'))
glyph.draw(TransformPen(path, (scale, 0, 0, -scale, offset_x, offset_y)))
(ROOT / 'packaging' / 'kinora.svg').write_text(
	'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {0} {0}"><rect width="{0}" height="{0}" rx="{1}" fill="{2}"/>'
	'<path fill="{3}" d="{4}"/></svg>\n'.format(SIZE, RADIUS, LAMP, ON_LAMP, path.getCommands()), encoding = 'utf-8')

# the same drawing for Windows, drawn large and scaled down per icon size
font.flavor = None
ttf = io.BytesIO()
font.save(ttf)
big = 1024
factor = big / SIZE
image = Image.new('RGBA', (big, big), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((0, 0, big - 1, big - 1), radius = RADIUS * factor, fill = LAMP)
ttf.seek(0)
pil_font = ImageFont.truetype(ttf, size = round(font['head'].unitsPerEm * scale * factor))
left, top, right, bottom = pil_font.getbbox('K')
draw.text(((big - (right - left)) / 2 - left, (big - (bottom - top)) / 2 - top), 'K', font = pil_font, fill = ON_LAMP)
image.resize((256, 256), Image.LANCZOS).save(ROOT / 'packaging' / 'windows' / 'kinora.ico', sizes = [ (size, size) for size in ICO_SIZES ])
