"""Choose numeric pixels from the already bound OCR result; no new recognition."""
import math

from visual_members import analyze_member_visual, numeric_member_label, rect, same_name_bounds


def numeric_guard(report, plan):
    number=plan['number'];chosen=plan['name_bounds']
    analysis=analyze_member_visual(report,number)
    if (not analysis['usable'] or analysis['list_matches']!=1
            or analysis['list_candidates']!=[chosen] or plan['action']!='click_once'):
        raise ValueError('目标数字区域与当前选人证据不一致，停止，不点击')
    capture=rect(report['capture']);scale=float(report['scale'])
    areas=[capture,rect(report['regions']['list']),rect(report['regions']['viewport'])]
    def screen(box):
        if len(box)!=4 or any(len(p)!=2 for p in box):raise ValueError('数字识别边界无效')
        xs=[float(p[0]) for p in box];ys=[float(p[1]) for p in box]
        if not all(math.isfinite(v) for v in xs+ys):raise ValueError('数字识别边界无效')
        return dict(left=capture[0]+min(xs)/scale,top=capture[1]+min(ys)/scale,
            width=(max(xs)-min(xs))/scale,height=(max(ys)-min(ys))/scale)
    def valid(bounds):
        x,y,w,h=rect(bounds)
        return same_name_bounds(bounds,chosen) and all(x>=a[0] and y>=a[1]
            and x+w<=a[0]+a[2] and y+h<=a[1]+a[3] for a in areas)
    def result(bounds,source):
        return {'numeric_bounds':bounds,'numeric_text':number,'numeric_source':source}
    # The selected Windows word is often tighter than the recognition line.
    # Use its actual OCR coordinates, without counting separate ink bands.
    for word in report['ocr'].get('words',[]):
        if str(word.get('text','')).strip()!=number:continue
        x,y,w,h=rect(word)
        bounds=dict(left=capture[0]+x/scale,top=capture[1]+y/scale,width=w/scale,height=h/scale)
        if bounds==chosen and valid(bounds):return result(bounds,'windows_numeric')
    backup=report.get('backup_ocr') or {}
    rapid=analysis.get('rapid_list_candidates',[])
    def matched_parent(word):
        bounds=screen(word['box'])
        return (numeric_member_label(word['text'])==number and bounds in rapid
            and same_name_bounds(bounds,chosen))
    for word in backup.get('detections',[]):
        if str(word['text']).strip()==number and matched_parent(word) and valid(screen(word['box'])):
            return result(screen(word['box']),'rapid_numeric')
    # A combined number+emoji line uses character coordinates from the SAME
    # recognition pass. Only the complete prefix digits enter the pixel guard.
    for line in backup.get('member_character_boxes',[]):
        parent={'text':line.get('parent_text'),'box':line.get('parent_box')}
        if not any(parent['text']==d['text'] and parent['box']==d['box'] for d in backup.get('detections',[])):
            continue
        if not matched_parent(parent):continue
        chars=line.get('characters',[])
        if ''.join(str(c.get('text','')) for c in chars)!=''.join(str(parent['text']).split()):continue
        digits=chars[:len(number)]
        if ''.join(str(c.get('text','')) for c in digits)!=number:continue
        if any(len(c['text'])!=1 or not math.isfinite(float(c['score']))
               or not 0<=float(c['score'])<=1 for c in digits):continue
        parent_rect=rect(screen(parent['box']));boxes=[rect(screen(c['box'])) for c in digits]
        if any(x<parent_rect[0] or y<parent_rect[1] or x+w>parent_rect[0]+parent_rect[2]
               or y+h>parent_rect[1]+parent_rect[3] for x,y,w,h in boxes):continue
        if any(boxes[i][0]>=boxes[i+1][0] for i in range(len(boxes)-1)):continue
        left=min(b[0] for b in boxes);top=min(b[1] for b in boxes)
        bounds=dict(left=left,top=top,width=max(b[0]+b[2] for b in boxes)-left,
            height=max(b[1]+b[3] for b in boxes)-top)
        if valid(bounds):return result(bounds,'rapid_numeric_characters')
    raise ValueError('当前识别结果缺少独立的目标数字区域，停止，不比较头像或表情')
