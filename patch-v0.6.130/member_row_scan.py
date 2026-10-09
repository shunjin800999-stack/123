"""Bound sequential OCR crops to Telegram's scaled member-list rows."""
import math

from visual_members import rect


def member_row_crops(report, width, height):
    capture=rect(report['capture']);scale=float(report['scale'])
    if scale!=2 or (width,height)!=(int(capture[2]*scale),int(capture[3]*scale)):
        raise ValueError('逐行识别截图尺寸与当前窗口不一致')
    listing=rect(report['regions']['list']);view=rect(report['regions']['viewport'])
    search=rect(report['regions']['search'])
    # The supported Qt layout has 20 units of top padding and 56-unit rows
    # when InputField::Inner is 25 units high. Follow its actual UI scale.
    unit=search[3]/25.0
    if not .4<=unit<=4 or listing[1]<view[1]-.5:
        raise ValueError('无法确认搜索列表从第一行开始，停止逐行识别')
    left=max(0,math.ceil((max(capture[0],listing[0],view[0])-capture[0])*scale))
    right=min(width,math.floor((min(capture[0]+capture[2],listing[0]+listing[2],view[0]+view[2])-capture[0])*scale))
    top=max(capture[1],listing[1],view[1])
    bottom=min(capture[1]+capture[3],listing[1]+listing[3],view[1]+view[3])
    if right<=left or bottom<=top:raise ValueError('搜索列表没有可见行')
    crops=[]
    for index in range(1,41):
        start=listing[1] if index==1 else listing[1]+20*unit+(index-1)*56*unit
        end=listing[1]+20*unit+index*56*unit
        if start>=bottom:break
        # Two pixels of overlap preserve anti-aliased top edges; they cannot
        # include the preceding row's name/status, which are much higher.
        start=max(top,start-(0 if index==1 else 2*unit))
        bounds=[left,max(0,math.floor((start-capture[1])*scale)),right,
            min(height,math.floor((min(bottom,end)-capture[1])*scale))]
        if bounds[3]>bounds[1]:crops.append(bounds)
    return crops


def first_member_strip(report, width, height):
    """Windows OCR reads title/header plus row one, never row two."""
    first=member_row_crops(report,width,height)[0]
    return [0,0,width,first[3]]


def matched_member_area(report):
    """Validate the worker's chosen crop against this exact bound frame."""
    scan=(report.get('backup_ocr') or {}).get('member_row_scan')
    if scan is None:return None
    if scan.get('policy')!='first_then_fallback' or scan.get('number')!=report.get('member_row_target'):
        raise ValueError('逐行识别结果与当前目标数字不一致')
    capture=rect(report['capture']);scale=float(report['scale'])
    crops=member_row_crops(report,int(capture[2]*scale),int(capture[3]*scale))
    index=scan.get('matched_row')
    if index is None:return ()
    if type(index) is not int or not 1<=index<=len(crops) or scan.get('crop_bounds')!=crops[index-1]:
        raise ValueError('逐行识别结果没有绑定当前行范围')
    l,t,r,b=crops[index-1]
    return (capture[0]+l/scale,capture[1]+t/scale,(r-l)/scale,(b-t)/scale)
