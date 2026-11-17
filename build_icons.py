"""Developer-only icon generator. Install Pillow to regenerate; runtime does not need it."""
from PIL import Image, ImageDraw
from pathlib import Path
import math

OUT = Path(__file__).parent / 'assets'
OUT.mkdir(exist_ok=True)
S = 256


def make(name):
    im = Image.new('RGBA', (S, S))
    mask = Image.new('L', (S, S))
    m = ImageDraw.Draw(mask)
    if name in ('app', 'folder'):
        m.rounded_rectangle((20, 52, 130, 125), 15, fill=255)
        m.rounded_rectangle((20, 78, 236, 216), 22, fill=255)
        top, bottom = (49, 195, 255), (0, 91, 218)
    elif name == 'error':
        m.polygon([(128, 24), (242, 224), (14, 224)], fill=255)
        top, bottom = (255, 195, 63), (235, 80, 0)
    elif name == 'settings':
        pts=[]
        for i in range(64):
            r = 108 if i % 8 in (1, 2, 3, 4) else 88
            a = i * math.pi / 32
            pts.append((128 + r * math.cos(a), 128 + r * math.sin(a)))
        m.polygon(pts, fill=255)
        top, bottom = (156, 175, 195), (64, 84, 107)
    else:
        m.rounded_rectangle((32, 22, 224, 234), 30, fill=255)
        top, bottom = ((73, 215, 166), (0, 127, 95)) if name == 'server' else ((80, 187, 255), (0, 93, 211))
    for y in range(S):
        t=y/(S-1)
        c=tuple(round(a*(1-t)+b*t) for a,b in zip(top,bottom))+(255,)
        ImageDraw.Draw(im).line((0,y,S,y), fill=c)
    im.putalpha(mask)
    d=ImageDraw.Draw(im)
    white=(255,255,255,245)
    if name in ('app','sync'):
        d.arc((67,67,189,189), 208, 360, fill=white, width=14)
        d.polygon([(176,59),(193,97),(153,96)], fill=white)
        d.arc((67,67,189,189), 28, 180, fill=white, width=14)
        d.polygon([(80,197),(63,159),(103,160)], fill=white)
    elif name=='folder':
        d.line((50,102,205,102), fill=(175,231,255,230), width=7)
    elif name=='server':
        for y in (76,133,190):
            d.rounded_rectangle((58,y-18,198,y+18), 10, fill=(0,83,65,100))
            d.ellipse((73,y-5,83,y+5), fill=white)
            d.line((105,y,179,y), fill=white,width=6)
    elif name=='settings':
        d.ellipse((87,87,169,169), fill=white)
        d.ellipse((107,107,149,149), fill=(73,94,117))
    elif name=='logs':
        d.polygon([(170,22),(224,76),(170,76)], fill=(188,232,255))
        for y,x in [(100,189),(137,189),(174,157)]:
            d.rounded_rectangle((66,y,x,y+10),5,fill=white)
    elif name=='save':
        d.rounded_rectangle((69,39,186,98),8,fill=white)
        d.rounded_rectangle((67,130,189,214),10,fill=white)
        d.rectangle((150,47,169,86),fill=(33,137,226))
    elif name=='check':
        d.line([(65,131),(107,172),(193,80)],fill=white,width=19)
    elif name=='error':
        d.rounded_rectangle((119,91,137,160),8,fill=white)
        d.ellipse((118,179,138,199),fill=white)
    return im

for name in ('app','folder','server','sync','settings','logs','save','check','error'):
    im=make(name)
    im.resize((32,32),Image.Resampling.LANCZOS).save(OUT/(name+'.png'))
    if name=='app':
        im.save(OUT/'app.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])

# Custom high-resolution checkbox indicators, including a transparent text gap.
# At 100% Windows scale the 96x84 image is subsampled to 32x28:
# the visible box is 22x22 pixels, with 10 pixels to the label.
for state in ('off', 'on', 'disabled_off', 'disabled_on'):
    selected = state.endswith('on')
    disabled = state.startswith('disabled')
    im = Image.new('RGBA', (96, 84))
    d = ImageDraw.Draw(im)
    color = '#a8b6c7' if disabled else '#0078d4'
    d.rounded_rectangle((0, 9, 65, 74), radius=12,
                        fill=color if selected else '#ffffff',
                        outline=color if selected else '#9ca9ba' if disabled else '#52657c', width=6)
    if selected:
        d.line([(16, 41), (28, 54), (50, 29)], fill='white', width=8, joint='curve')
    im.save(OUT / ('checkbox_' + state + '.png'))
