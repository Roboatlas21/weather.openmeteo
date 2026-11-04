"""Build the native Kodi layout and small code-drawn UI textures."""
from pathlib import Path
import math
import xml.etree.ElementTree as E
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / 'resources/skins/Default/media'
XML = ROOT / 'resources/skins/Default/1080i/weather-fullscreen.xml'


def sub(parent, name, value=None, **attrs):
    item = E.SubElement(parent, name, attrs)
    if value is not None:
        item.text = str(value)
    return item


def prop(key):
    return '$INFO[Window.Property(Weather.'+key+')]'


def word(id):
    return '$ADDON[weather.openmeteo {}]'.format(id)


def control(parent, kind, id, x, y, w, h, **values):
    c = sub(parent, 'control', type=kind, **({'id':str(id)} if id else {}))
    for tag, val in dict(left=x, top=y, width=w, height=h, **values).items():
        sub(c, tag, val)
    return c


def image(parent, id, x, y, w, h, texture, aspect='stretch', visible=None):
    c = control(parent, 'image', id, x, y, w, h, aspectratio=aspect)
    sub(c, 'texture', texture)
    if visible:
        sub(c, 'visible', visible)
    return c


def label(parent, id, x, y, w, h, text, font='font13', color='FFF2F6FA', **extra):
    return control(parent, 'label', id, x, y, w, h, label=text, font=font,
                   textcolor=color, aligny='center', **extra)


def button(parent, id, x, y, w, h, text, left, right, up, down):
    c = control(parent, 'button', id, x, y, w, h, label=text, font='font13',
                textcolor='FFF0F7FC', focusedcolor='FFFFFFFF', align='center', aligny='center',
                onleft=left, onright=right, onup=up, ondown=down)
    sub(c, 'texturenofocus', 'transparent.png')
    sub(c, 'texturefocus', 'focus.png', border='12')
    return c


def group(parent, view):
    c = sub(parent, 'control', type='group')
    sub(c, 'visible', 'String.IsEqual(Window.Property(Weather.View),'+view+')')
    return c


def build_xml():
    window = E.Element('window')
    sub(window, 'defaultcontrol', 100, always='true')
    sub(window, 'allowoverlay', 'no')
    coordinates = sub(window, 'coordinates')
    sub(coordinates, 'system', 1)
    sub(coordinates, 'origin', x='0', y='0')
    controls = sub(window, 'controls')
    image(controls, 1, 0, 0, 1920, 1080, 'background.png')
    mapgroup = group(controls, 'maps')
    image(mapgroup, 2, 0, 125, 1920, 880, prop('Map.Base'), 'scale')
    overlay = image(mapgroup, 3, 0, 125, 1920, 880, prop('Map.Image'), 'scale')
    sub(overlay, 'colordiffuse', 'CCFFFFFF')
    image(controls, None, 0, 0, 1920, 128, 'header.png')
    button(controls, 10, 60, 25, 840, 85, prop('Location')+'  ▾', 14, 11, 15, 100)
    for i, (x, w) in enumerate(((970,220),(1200,170),(1380,190),(1580,165))):
        button(controls, 11+i, x, 38, w, 64, word(32701+i), 10 if i==0 else 10+i,
               11 if i==3 else 12+i, 11+i, 100)
    label(controls, 40, 1750, 45, 125, 48, prop('Clock'), 'font14', align='right')

    overview = group(controls, 'overview')
    label(overview, None, 76, 140, 700, 40, word(32700), 'font12', 'FF93B7CD')
    label(overview, 50, 68, 184, 520, 175, prop('Temp'), 'font60')
    image(overview, None, 620, 165, 240, 200, prop('Icon'), 'keep')
    label(overview, None, 76, 365, 820, 65, prop('Condition'), 'font45')
    label(overview, None, 76, 431, 820, 44, word(32715)+' '+prop('Feels')+'   '+prop('Range'), 'font14', 'FFB4C8D5')
    label(overview, None, 76, 485, 820, 50, prop('Later'), 'font14')
    for x, title, value in ((76,32709,'Wind'),(360,32710,'Humidity'),(650,32717,'Sunset')):
        label(overview, None, x, 555, 260, 32, word(title), 'font12', 'FF8DAEC4')
        label(overview, None, x, 590, 260, 45, prop(value), 'font14')
    image(overview, None, 985, 156, 865, 440, prop('Map.Base'), 'scale')
    overlay = image(overview, None, 985, 156, 865, 440, prop('Map.Image'), 'scale')
    sub(overlay, 'colordiffuse', 'CCFFFFFF')
    image(overview, None, 985, 156, 865, 58, 'panel.png')
    label(overview, None, 1010, 164, 420, 44, prop('Map.Heading'), 'font14')
    label(overview, None, 1440, 165, 385, 42, prop('Map.Time'), 'font12', align='right')
    image(overview, None, 985, 544, 865, 52, 'panel.png')
    label(overview, None, 1000, 546, 830, 48, word(32705)+'  →', 'font14', align='center')
    label(overview, None, 1000, 300, 830, 100, prop('Map.Status'), 'font14', align='center', wrapmultiline='true')
    button(overview, 100, 980, 151, 875, 450, '', 10, 14, 14, 101)
    label(overview, None, 985, 609, 865, 32, prop('Map.Source'), 'font10', 'FF9DB8C9', align='right')
    label(overview, None, 76, 647, 700, 45, word(32707), 'font14')
    for i in range(7):
        x = 87+i*252
        label(overview, None, x, 699, 220, 40, prop('Hour.{}.time'.format(i)), 'font13', align='center')
        image(overview, None, x+5, 750, 82, 73, prop('Hour.{}.icon'.format(i)), 'keep')
        label(overview, None, x+93, 744, 126, 44, prop('Hour.{}.temp'.format(i)), 'font14')
        label(overview, None, x+93, 791, 126, 35, prop('Hour.{}.rain'.format(i)), 'font12', 'FF80D7FF')
    button(overview, 101, 65, 692, 1790, 145, '', 101, 101, 100, 102)
    image(overview, None, 76, 854, 1768, 2, 'line.png')
    for i in range(7):
        x=88+i*252
        image(overview, None, x, 881, 80, 80, prop('Day.{}.icon'.format(i)), 'keep')
        label(overview, None, x+90, 878, 145, 42, prop('Day.{}.day'.format(i)), 'font13')
        label(overview, None, x+90, 923, 145, 38, prop('Day.{}.high'.format(i))+' / '+prop('Day.{}.low'.format(i)), 'font12', 'FFB4C8D5')
    button(overview, 102, 65, 870, 1790, 110, '', 102, 102, 101, 15)

    table = sub(controls, 'control', type='group')
    sub(table, 'visible', 'String.IsEqual(Window.Property(Weather.View),hourly) | String.IsEqual(Window.Property(Weather.View),daily)')
    label(table, None, 76, 153, 900, 66, prop('Heading'), 'font45')
    for x,w,text in ((90,270,word(32742)),(390,500,word(32744)),(940,290,word(32731)),(1275,240,word(32745)),(1550,280,word(32709))):
        label(table, None, x, 239, w, 48, text, 'font13', 'FF90B5CB')
    c=control(table, 'list', 300, 65, 300, 1790, 675, onleft=11, onright=14, onup=12, ondown=15,
              pagecontrol=310, scrolltime=180, orientation='vertical')
    for tag in ('itemlayout','focusedlayout'):
        item=sub(c, tag, height='84', width='1790')
        image(item, None, 0, 0, 1790, 82, 'row-focus.png' if tag=='focusedlayout' else 'row.png')
        label(item, None, 25, 8, 280, 40, '$INFO[ListItem.Property(rowdate)]', 'font13')
        label(item, None, 25, 44, 280, 32, '$INFO[ListItem.Property(rowtime)]', 'font10', 'FF9DB8C9')
        image(item, None, 315, 9, 65, 65, '$INFO[ListItem.Art(icon)]', 'keep')
        label(item, None, 395, 8, 470, 40, '$INFO[ListItem.Property(condition)]', 'font13')
        label(item, None, 395, 44, 470, 32, '$INFO[ListItem.Property(detail)]', 'font10', 'FF9DB8C9')
        label(item, None, 875, 12, 310, 58, '$INFO[ListItem.Property(temperature)]', 'font14')
        label(item, None, 1210, 12, 220, 58, '$INFO[ListItem.Property(rain)]', 'font14', 'FF80D7FF')
        label(item, None, 1485, 12, 285, 58, '$INFO[ListItem.Property(wind)]', 'font13')
    label(table, None, 100, 480, 1720, 120, prop('Empty'), 'font14', align='center')
    control(table, 'scrollbar', 310, 1867, 300, 9, 675, texturesliderbackground='row.png',
            texturesliderbar='line.png', texturesliderbarfocus='line.png', textureslidernib='line.png', textureslidernibfocus='line.png', orientation='vertical', showonepage='false')

    image(mapgroup, None, 55, 158, 365, 545, 'panel.png')
    label(mapgroup, None, 80, 174, 310, 45, word(32746), 'font14')
    for i in range(5):
        button(mapgroup, 200+i, 70, 234+i*69, 335, 62, word(32730+i), 200+i, 224, 14 if i==0 else 199+i, 210 if i==4 else 201+i)
    button(mapgroup, 210, 70, 616, 160, 65, word(32735), 211, 211, 204, 220)
    button(mapgroup, 211, 239, 616, 166, 65, word(32736), 210, 221, 204, 220)
    image(mapgroup, None, 1575, 160, 300, 105, 'panel.png')
    label(mapgroup, None, 1590, 170, 270, 45, prop('Map.Time'), 'font14', align='center')
    label(mapgroup, None, 1590, 215, 270, 32, prop('Map.Units'), 'font12', 'FF93B7CD', align='center')
    button(mapgroup, 224, 1575, 280, 300, 58, word(32747), 200, 221, 14, 221)
    image(mapgroup, None, 1630, 350, 245, 400, prop('Map.Legend'), 'keep')
    label(mapgroup, None, 455, 395, 1090, 120, prop('Map.Status'), 'font14', align='center', wrapmultiline='true')
    image(mapgroup, None, 100, 876, 1720, 125, 'panel.png')
    button(mapgroup, 220, 115, 894, 180, 86, word(32726), 223, 221, 210, 14)
    button(mapgroup, 221, 310, 891, 1190, 92, '', 221, 221, 200, 14)
    label(mapgroup, None, 340, 901, 1110, 40, prop('Map.Range'), 'font12')
    control(mapgroup, 'progress', 225, 340, 958, 1080, 8, texturebg='line.png', lefttexture='progress.png', midtexture='progress.png', righttexture='progress.png', overlaytexture='transparent.png')
    button(mapgroup, 222, 1515, 894, 95, 86, '‹', 221, 223, 211, 14)
    button(mapgroup, 223, 1620, 894, 95, 86, '›', 222, 220, 211, 14)
    label(mapgroup, None, 1720, 906, 90, 60, prop('Map.Count'), 'font12', align='center')

    legend = sub(controls, 'control', type='group')
    sub(legend, 'visible', 'String.IsEqual(Window.Property(Weather.View),maps) + String.IsEqual(Window.Property(Weather.LegendExpanded),true)')
    image(legend, None, 0, 128, 1920, 880, 'header.png')
    image(legend, None, 540, 190, 840, 780, prop('Map.Legend'), 'keep')
    label(legend, None, 440, 135, 1040, 50, word(32748), 'font14', align='center')
    button(legend, 240, 400, 135, 1120, 850, '', 240, 240, 240, 240)

    image(controls, None, 0, 1012, 1920, 68, 'header.png')
    label(controls, None, 76, 1025, 920, 42, prop('Hint'), 'font12', 'FFB4C8D5')
    source = label(controls, None, 1020, 1025, 820, 42, prop('Map.Source'), 'font10', 'FF9DB8C9', align='right')
    sub(source,'visible','String.IsEqual(Window.Property(Weather.View),maps)')
    status = label(controls, None, 1050, 1025, 560, 42, prop('Status'), 'font10', 'FF9DB8C9', align='right')
    sub(status,'visible','!String.IsEqual(Window.Property(Weather.View),maps)')
    b=button(controls, 15, 1640, 1020, 200, 52, word(32711), 102, 10, 102, 10)
    sub(b,'visible','!String.IsEqual(Window.Property(Weather.View),maps)')
    # Every tab has a valid Down target in every view, including hidden pages.
    for c in controls.findall('control'):
        if c.get('id') in ('10', '11', '12', '13', '14'):
            c.remove(c.find('ondown'))
            for view, target in (('overview',100),('hourly',300),('daily',300),('maps',200)):
                sub(c, 'ondown', 'SetFocus({})'.format(target), condition='String.IsEqual(Window.Property(Weather.View),'+view+')')
        if c.get('id') == '15':
            c.remove(c.find('onup'))
            sub(c,'onup','SetFocus(102)',condition='String.IsEqual(Window.Property(Weather.View),overview)')
            sub(c,'onup','SetFocus(300)',condition='!String.IsEqual(Window.Property(Weather.View),overview)')
    E.indent(window, space='  ')
    XML.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'+E.tostring(window, encoding='unicode')+'\n')


def textures():
    MEDIA.mkdir(parents=True, exist_ok=True)
    bg=Image.new('RGB',(1920,1080))
    pixels=bg.load()
    for y in range(1080):
        for x in range(1920):
            glow=math.exp(-(((x-570)/900)**2+((y-150)/430)**2))
            pixels[x,y]=(int(8+12*glow),int(15+22*glow),int(24+30*glow))
    bg.save(MEDIA/'background.png',optimize=True)
    for name,color in [('panel',(13,25,36,235)),('header',(7,16,25,247)),('row',(17,29,41,220)),
                       ('row-focus',(33,65,86,255)),('line',(85,121,145,160)),('progress',(128,215,255,255)),('transparent',(0,0,0,0))]:
        Image.new('RGBA',(32,32),color).save(MEDIA/(name+'.png'))
    focus=Image.new('RGBA',(48,48));d=ImageDraw.Draw(focus)
    d.rounded_rectangle((2,2,45,45),radius=11,outline='#8BD9FF',width=3)
    focus.save(MEDIA/'focus.png')
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',80)
    small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',35)
    for name,title,subtitle in [('weather','Weather','Forecast · Hourly · 10 days'),('maps','Weather maps','Radar · Temperature · Wind')]:
        im=bg.resize((960,540));d=ImageDraw.Draw(im)
        if name=='weather':
            d.ellipse((630,120,770,260),fill='#CFE8F5');d.ellipse((700,150,850,280),fill='#B3CDDF');d.rounded_rectangle((600,210,855,285),radius=35,fill='#CFE8F5')
            for x in (650,725,800):d.line((x,315,x-25,370),fill='#80D7FF',width=10)
        else:
            for r in (55,105,155):d.ellipse((735-r,240-r,735+r,240+r),outline='#4A7A96',width=3)
            d.pieslice((580,85,890,395),220,310,fill='#21799E');d.ellipse((721,226,749,254),fill='#A8E7FF')
        d.text((55,360),title,font=font,fill='white');d.text((58,465),subtitle,font=small,fill='#A7C5D8')
        im.save(MEDIA/('widget-'+name+'.png'),optimize=True)


if __name__=='__main__':
    build_xml()
    textures()
