"""Run the production PowerShell/C# name guard on actual rendered digit pixels."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageFont

BASE=Path(__file__).parent
POWERSHELL=os.environ.get('TELEGRAM_TEST_POWERSHELL') or shutil.which('pwsh') or shutil.which('powershell')
FONTS=[p for p in (Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'),
    Path(r'C:\Windows\Fonts\segoeui.ttf')) if p.is_file()]


@unittest.skipUnless(POWERSHELL and FONTS,'Requires PowerShell and a test font')
class NumericNameGuardTests(unittest.TestCase):
    def test_production_guard_excludes_animation_but_rejects_changed_digits(self):
        cases=[]
        for font_path in FONTS:
            for size in (18,20,24,28):
                font=ImageFont.truetype(str(font_path),size)
                for number in ('0','1','12','001','2416','2429','123456'):
                    box=font.getbbox(number);width=box[2]-box[0];height=box[3]-box[1]
                    image=Image.new('RGB',(width,height),'white')
                    ImageDraw.Draw(image).text((-box[0],-box[1]),number,font=font,fill=(20,20,20))
                    pixels=[]
                    for y in range(height):
                        for x in range(width):
                            r,g,b=image.getpixel((x,y))
                            if (r,g,b)!=(255,255,255):pixels.append([x,y,(r<<16)+(g<<8)+b])
                    cases.append({'name':f'{font_path.name}/{size}/{number}','number':number,
                        'width':width,'height':height,'pixels':pixels})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'glyphs.json';path.write_text(json.dumps(cases),encoding='utf-8')
            run=subprocess.run([POWERSHELL,'-NoProfile','-NonInteractive','-File',
                str(BASE/'test_numeric_member_guard.ps1'),'-GlyphCasesPath',str(path)],
                capture_output=True,text=True,timeout=90)
        self.assertEqual(run.returncode,0,run.stdout+'\n'+run.stderr)
        self.assertIn(f'{35+2*len(cases)} production numeric pixel-guard scenarios passed',run.stdout)


if __name__=='__main__':unittest.main()
