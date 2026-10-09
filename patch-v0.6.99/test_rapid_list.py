"""Regression for v0.6.0: Windows OCR omitted names RapidOCR read exactly.

Only sanitized geometry and relevant OCR words are retained from the user's
report. Imported evidence is classified offline; no Windows input is run.
"""
import copy
import unittest

from backup_ocr import FRAME_KEYS
from visual_members import analyze_member_visual, plan_member_selection, verify_member_selection


def detection(text,box,score=.999):
    left,top,right,bottom=box
    return {'text':text,'score':score,'box':[[left,top],[right,top],[right,bottom],[left,bottom]]}


def sample():
    r={'ok':True,'read_only':True,'scope':'member_visual','scale':2,
        'window_handle':100,'process_id':200,'dialog_runtime_id':'test-dialog','final_invite_clicked':False,
        'capture':{'left':748,'top':218,'width':364,'height':580},
        'regions':{'list':{'left':748,'top':310,'width':364,'height':244},
            'viewport':{'left':748,'top':310,'width':364,'height':434},
            'header':{'left':748,'top':266,'width':364,'height':44},
            'search':{'left':794,'top':279,'width':268,'height':25}},
        'button_regions':{'add':{'left':1045,'top':754,'width':57,'height':34},
            'cancel':{'left':964,'top':754,'width':75,'height':34}},
        'ocr':{'available':True,'text':'Ad d Members last seen 1 hour ago Cancel Ad d',
            'words':[{'text':text,'left':left,'top':top,'width':width,'height':height} for text,left,top,width,height in
                [('last',149,383,40,21),('seen',197,389,53,15),('1',260,385,7,18),
                 ('hour',279,383,57,21),('ago',342,389,43,21),('11',149,455,23,19),('H',183,455,17,19)]]},
        'backup_ocr':{'ok':True,'image_sha256':'a'*64,'final_invite_clicked':False,
            'accepted':[],'rejected':[],'selected_numbers':[],'other_header_words':[],
            'detections':[detection('1',(146,228,161,253)),
                detection('last seen today at 6:07',(142,266,414,299)),
                detection('1.',(143,337,173,367)),detection('last seen 1 hour ago',(141,377,389,413)),
                detection('11 H',(143,448,207,480),.89236),detection('1H',(45,461,101,505)),
                detection('last seen today at 9:08',(141,489,416,524)),
                detection('1f',(142,558,180,592)),detection('last seen today at 7:48',(142,603,415,635))]}}
    bind(r)
    return r


def bind(r):
    r['selection_header_ocr']={'ok':True,'used_for_selection':True,'image_sha256':'a'*64,
        'frame':{key:copy.deepcopy(r[key]) for key in FRAME_KEYS}}


def portuguese_sample():
    """Sanitized OCR and geometry from the B11 stopped-before-click report."""
    r=sample()
    r['capture']={'left':994,'top':234,'width':364,'height':580}
    r['regions']={'list':{'left':994,'top':326,'width':364,'height':76},
        'viewport':{'left':994,'top':326,'width':364,'height':434},
        'header':{'left':994,'top':282,'width':364,'height':44},
        'search':{'left':1040,'top':295,'width':268,'height':25}}
    r['button_regions']={'add':{'left':1253,'top':770,'width':95,'height':34},
        'cancel':{'left':1158,'top':770,'width':89,'height':34}}
    # Windows OCR saw the status date but omitted the complete contact name.
    r['ocr']={'available':True,'text':'Adicionar Membros 8 / 200000 visto em 2026 / 9 / 27 Cancelar Adicionar',
        'words':[{'text':text,'left':left,'top':273,'width':width,'height':19}
            for text,left,width in [('vi',147,19),('sto',169,36),('em',213,35),
                ('2026',257,54),('/',312,9),('9',323,12),('/',336,9),('27',347,26)]]}
    r['backup_ocr']['detections']=[detection('11',(142,223,180,257),.99992),
        detection('visto em 2026/9/27',(142,266,377,296),.99992)]
    bind(r)
    return r


def portuguese_minutes_sample():
    """Sanitized B19 geometry: Windows OCR omitted the name, RapidOCR did not."""
    r=portuguese_sample()
    r['ocr']={'available':True,
        'text':'Adicionar Membros 3 / 200000 P 19 vi stO hå 58 minutos Cancelar Adicionar',
        'words':[{'text':text,'left':left,'top':top,'width':width,'height':height}
            for text,left,top,width,height in [('P',38,127,28,29),('19',93,131,27,19),
                ('vi',147,273,19,19),('stO',169,274,36,18),('hå',213,271,27,21),
                ('58',249,273,26,19),('minutos',283,273,98,19)]]}
    search=detection('19',(29,117,128,162),.99626)
    r['backup_ocr']['rejected']=[dict(search,pale_gray_border=0.,dark_ink_fraction=.0056)]
    r['backup_ocr']['detections']=[search,detection('19',(142,223,182,257),1.),
        {'text':'visto há 58 minutos','score':.99995,
            'box':[[143,265],[386,268],[386,298],[142,296]]}]
    bind(r)
    return r


def avatar_number_sample():
    """Sanitized A24 report: an avatar's 2 overlapped the exact 24 name line."""
    r=sample();r['regions']['list']['height']=76
    r['ocr']={'available':True,'text':'Ad d Members 7 / 200000 last seen 2026 / 9 / 22 Cancel Ad d',
        'words':[{'text':text,'left':left,'top':top,'width':width,'height':height}
            for text,left,top,width,height in [('last',149,271,40,21),('seen',197,277,53,15),
                ('2026',259,273,54,19),('/',314,274,9,17),('9',325,273,12,19),
                ('/',338,274,9,17),('22',349,273,26,19)]]}
    search=detection('24',(31,120,128,161),.99744)
    r['backup_ocr']['rejected']=[dict(search,pale_gray_border=0.,dark_ink_fraction=.01056)]
    r['backup_ocr']['detections']=[search,
        {'text':'24','score':.99999,'box':[[141,222],[182,224],[180,258],[139,255]]},
        detection('2',(54,237,90,280),.99981),
        detection('last seen 2026/9/22',(143,266,379,296),.99963)]
    bind(r)
    return r


class RapidListTests(unittest.TestCase):
    def test_observed_avatar_digit_does_not_merge_with_exact_24_text(self):
        plan=plan_member_selection(avatar_number_sample(),'24')
        self.assertEqual(plan['action'],'click_once')
        self.assertEqual(plan['analysis']['list_matches'],1)
        self.assertEqual(plan['analysis']['selected_numbers'],[])
        self.assertEqual((plan['x'],plan['y']),(828,338))

    def test_left_avatar_contents_cannot_change_the_contact_candidate(self):
        for avatar in ('2','24','123','I','24.'):
            with self.subTest(avatar=avatar):
                r=avatar_number_sample();r['backup_ocr']['detections'][2]['text']=avatar
                self.assertEqual(analyze_member_visual(r,'24')['list_matches'],1)
                r['backup_ocr']['detections'].pop(1)
                self.assertEqual(analyze_member_visual(r,'24')['list_matches'],0)
                with self.assertRaises(ValueError):plan_member_selection(r,'24')

    def test_fragments_in_or_overlapping_the_text_column_still_stop(self):
        for box in ((186,222,204,258),(125,222,135,258),(180,222,195,258)):
            r=avatar_number_sample();r['backup_ocr']['detections'].append(detection('2',box))
            with self.subTest(box=box):
                self.assertEqual(analyze_member_visual(r,'24')['list_matches'],0)
                with self.assertRaises(ValueError):plan_member_selection(r,'24')
        for label in ('224','024','24.','I4'):
            r=avatar_number_sample();r['backup_ocr']['detections'][1]['text']=label
            with self.subTest(label=label):
                with self.assertRaises(ValueError):plan_member_selection(r,'24')

    def test_avatar_exclusion_does_not_hide_a_second_exact_contact(self):
        r=avatar_number_sample();r['regions']['list']['height']=244;bind(r)
        r['backup_ocr']['detections']+=[detection('24',(142,337,182,373)),
            detection('last seen recently',(143,380,379,410)),detection('2',(54,352,90,395))]
        self.assertEqual(analyze_member_visual(r,'24')['list_matches'],2)
        with self.assertRaises(ValueError):plan_member_selection(r,'24')

    def test_avatar_exclusion_still_requires_status_confidence_and_binding(self):
        for kind in ('name_score','status_score','missing_status','duplicate_status','unaligned','binding'):
            r=avatar_number_sample();name=r['backup_ocr']['detections'][1];status=r['backup_ocr']['detections'][3]
            if kind=='name_score':name['score']=.89
            if kind=='status_score':status['score']=.8
            if kind=='missing_status':status['text']='unrelated 24'
            if kind=='duplicate_status':r['backup_ocr']['detections'].append(copy.deepcopy(status))
            if kind=='unaligned':status['box']=detection('',(220,266,456,296))['box']
            if kind=='binding':r['dialog_runtime_id']='other-dialog'
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):plan_member_selection(r,'24')

    def test_avatar_exclusion_uses_relative_live_text_geometry(self):
        r=avatar_number_sample()
        for area in [r['capture'],*r['regions'].values(),*r['button_regions'].values()]:
            area['left']+=180;area['top']+=100
        bind(r);plan=plan_member_selection(r,'24')
        self.assertEqual((plan['x'],plan['y']),(1008,438))

    def test_observed_b19_minutes_status_recovers_only_the_exact_contact_row(self):
        r=portuguese_minutes_sample()
        plan=plan_member_selection(r,'19')
        self.assertEqual(plan['action'],'click_once')
        self.assertEqual((plan['x'],plan['y']),(1075,354))
        self.assertEqual(plan['analysis']['list_matches'],1)
        self.assertEqual(plan['analysis']['selected_numbers'],[])

    def test_minutes_status_requires_the_complete_portuguese_label(self):
        for status in ('visto há 1 minuto','visto há 19 minutos','visto há 58 minutos'):
            with self.subTest(status=status):
                r=portuguese_minutes_sample();r['backup_ocr']['detections'][2]['text']=status
                self.assertEqual(analyze_member_visual(r,'19')['list_matches'],1)
        for status in ('há 58 minutos','visto há minutos','visto há 58 min',
                'visto há 58 minutos extra','outro visto há 58 minutos','visto ha 58 minutos'):
            with self.subTest(status=status):
                r=portuguese_minutes_sample();r['backup_ocr']['detections'][2]['text']=status
                with self.assertRaises(ValueError):plan_member_selection(r,'19')

    def test_minutes_status_cannot_supply_name_or_normalize_a_similar_name(self):
        for name in ('1','019','119','19.','I9','19 H'):
            with self.subTest(name=name):
                r=portuguese_minutes_sample();r['backup_ocr']['detections'][1]['text']=name
                with self.assertRaises(ValueError):plan_member_selection(r,'19')
        r=portuguese_minutes_sample();r['backup_ocr']['detections'].pop(1)
        r['backup_ocr']['detections'][1]['text']='visto há 19 minutos'
        self.assertEqual(analyze_member_visual(r,'19')['list_matches'],0)

    def test_minutes_status_keeps_geometry_confidence_and_capture_guards(self):
        for kind in ('avatar','far','unaligned','low_status','low_name','binding'):
            r=portuguese_minutes_sample();name,status=r['backup_ocr']['detections'][1:]
            if kind=='avatar':name['box']=detection('',(33,223,80,257))['box']
            if kind=='far':status['box']=detection('',(142,320,386,350))['box']
            if kind=='unaligned':status['box']=detection('',(240,265,484,298))['box']
            if kind=='low_status':status['score']=.8
            if kind=='low_name':name['score']=.8
            if kind=='binding':r['dialog_runtime_id']='changed'
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):plan_member_selection(r,'19')

    def test_two_minutes_status_rows_with_same_name_cannot_be_clicked(self):
        r=portuguese_minutes_sample();r['regions']['list']['height']=244;bind(r)
        r['backup_ocr']['detections']+=[detection('19',(142,337,182,371)),
            detection('visto há 1 minuto',(142,380,386,410))]
        self.assertEqual(analyze_member_visual(r,'19')['list_matches'],2)
        with self.assertRaises(ValueError):plan_member_selection(r,'19')

    def test_observed_missing_windows_name_is_recovered_as_exact_first_row(self):
        r=sample();before=copy.deepcopy(r);before.pop('selection_header_ocr')
        self.assertEqual(analyze_member_visual(before,'1')['list_matches'],0)
        p=plan_member_selection(r,'1')
        self.assertEqual(p['action'],'click_once');self.assertEqual((p['x'],p['y']),(824,338))
        self.assertEqual(p['analysis']['list_matches'],1)

    def test_legacy_names_punctuation_and_letters_are_never_normalized_to_one(self):
        for value in ('001','11','1.','1f','I','l'):
            r=sample();r['backup_ocr']['detections'][0]['text']=value
            self.assertEqual(analyze_member_visual(r,'1')['list_matches'],0,value)
        r=sample();r['backup_ocr']['detections'][0]['text']='001'
        self.assertEqual(analyze_member_visual(r,'001')['list_matches'],1)

    def test_avatar_or_number_in_status_is_not_a_name(self):
        r=sample();r['backup_ocr']['detections'][0]=detection('1',(45,228,85,260))
        self.assertEqual(analyze_member_visual(r,'1')['list_matches'],0)
        with self.assertRaises(ValueError):plan_member_selection(r,'1')

    def test_unaligned_far_away_missing_or_low_confidence_status_does_not_anchor_name(self):
        for kind in ('unaligned','far','missing','score'):
            r=sample();status=r['backup_ocr']['detections'][1]
            if kind=='unaligned':status['box']=detection('',(200,266,450,299))['box']
            if kind=='far':status['box']=detection('',(142,310,414,335))['box']
            if kind=='missing':status['text']='unrelated text'
            if kind=='score':status['score']=.8
            self.assertEqual(analyze_member_visual(r,'1')['list_matches'],0,kind)

    def test_duplicate_exact_names_are_not_hidden_by_fallback(self):
        r=sample();r['backup_ocr']['detections'][2]['text']='1'
        self.assertEqual(analyze_member_visual(r,'1')['list_matches'],2)
        with self.assertRaises(ValueError):plan_member_selection(r,'1')

    def test_windows_candidate_and_rapid_duplicate_from_another_row_stop(self):
        r=sample();r['ocr']['words'].append({'text':'1','left':146,'top':337,'width':15,'height':25})
        self.assertEqual(analyze_member_visual(r,'1')['list_matches'],2)
        with self.assertRaises(ValueError):plan_member_selection(r,'1')

    def test_same_row_seen_by_both_engines_counts_once(self):
        r=sample();r['ocr']['words'].append({'text':'1','left':149,'top':231,'width':7,'height':19})
        self.assertEqual(analyze_member_visual(r,'1')['list_matches'],1)
        self.assertEqual(plan_member_selection(r,'1')['action'],'click_once')

    def test_low_confidence_duplicate_never_allows_clicking_other_match(self):
        r=sample();r['backup_ocr']['detections'][2].update(text='1',score=.89)
        self.assertFalse(analyze_member_visual(r,'1')['usable'])
        with self.assertRaises(ValueError):plan_member_selection(r,'1')

    def test_split_digits_or_punctuation_on_name_line_are_not_partial_matches(self):
        for text in ('1','.'):
            r=sample();r['backup_ocr']['detections'].append(detection(text,(170,228,185,253)))
            self.assertEqual(analyze_member_visual(r,'1')['list_matches'],0)

    def test_changed_capture_binding_or_malformed_box_cannot_supply_candidates(self):
        for kind in ('hash','frame','box','score'):
            r=sample()
            if kind=='hash':r['selection_header_ocr']['image_sha256']='b'*64
            if kind=='frame':r['dialog_runtime_id']='other-dialog'
            if kind=='box':r['backup_ocr']['detections'][0]['box'][0][0]=float('nan')
            if kind=='score':r['backup_ocr']['detections'][0]['score']=float('inf')
            self.assertFalse(analyze_member_visual(r,'1')['usable'],kind)

    def test_incomplete_or_offscreen_rows_do_not_become_clicks(self):
        for top in (160,660):
            r=sample();r['backup_ocr']['detections'][0]['box']=detection('1',(146,top,161,top+30))['box']
            self.assertEqual(analyze_member_visual(r,'1')['list_matches'],0)

    def test_moving_window_uses_live_geometry_instead_of_fixed_coordinates(self):
        r=sample()
        for area in [r['capture'],*r['regions'].values(),*r['button_regions'].values()]:
            area['left']+=180;area['top']+=100
        bind(r);p=plan_member_selection(r,'1')
        self.assertEqual((p['x'],p['y']),(1004,438))

    def test_final_verification_requires_new_chip_and_unambiguous_remaining_rows(self):
        before=sample();after=copy.deepcopy(before)
        after['regions']['search'].update(left=850,width=252)
        accepted={'text':'1','score':.999,'pale_gray_border':.99,'dark_ink_fraction':.04,
            'box':[[100,120],[118,120],[118,145],[100,145]]}
        after['backup_ocr'].update(selected_numbers=['1'],accepted=[accepted]);bind(after)
        self.assertTrue(verify_member_selection(before,after,'1')['selection_verified'])
        duplicate=copy.deepcopy(after);duplicate['backup_ocr']['detections'][2]['text']='1'
        self.assertFalse(verify_member_selection(before,duplicate,'1')['selection_verified'])
        after['backup_ocr']['detections'].pop(0)
        self.assertTrue(verify_member_selection(before,after,'1')['selection_verified'])

    def test_observed_portuguese_missing_windows_11_is_recovered(self):
        r=portuguese_sample();unbound=copy.deepcopy(r);unbound.pop('selection_header_ocr')
        self.assertEqual(analyze_member_visual(unbound,'11')['list_matches'],0)
        plan=plan_member_selection(r,'11')
        self.assertEqual(plan['action'],'click_once')
        self.assertEqual((plan['x'],plan['y']),(1074,354))
        self.assertEqual(plan['analysis']['rapid_list_candidates'],
            [{'left':1065.0,'top':345.5,'width':19.0,'height':17.0}])

    def test_observed_portuguese_status_variants_anchor_the_exact_name(self):
        for status in ('visto em 2026/10/1','visto em 2026/10/2','visto em 2026/9/27',
                'visto em 2026/9/30','visto há 6 horas','visto na última semana','visto recentemente','online'):
            with self.subTest(status=status):
                r=portuguese_sample();r['backup_ocr']['detections'][1]['text']=status
                self.assertEqual(analyze_member_visual(r,'11')['list_matches'],1)

    def test_portuguese_long_ago_requires_exact_numeric_name(self):
        r=portuguese_sample();r['backup_ocr']['detections'][1]['text']='visto há muito tempo'
        self.assertEqual(plan_member_selection(r,'11')['action'],'click_once')
        for name in ('Conta Excluída','111','11.'):
            r['backup_ocr']['detections'][0]['text']=name
            with self.assertRaises(ValueError):plan_member_selection(r,'11')
        r=portuguese_sample();r['backup_ocr']['detections'][1]['text']='visto há muito tempo extra'
        with self.assertRaises(ValueError):plan_member_selection(r,'11')

    def test_portuguese_just_now_and_recently_status(self):
        for status in ('visto agora mesmo','visto recentemente'):
            r=portuguese_sample();r['backup_ocr']['detections'][1]['text']=status
            self.assertEqual(plan_member_selection(r,'11')['action'],'click_once')
        for status in ('agora mesmo','visto agora mesmo extra','visto recentemente 11'):
            r=portuguese_sample();r['backup_ocr']['detections'][1]['text']=status
            with self.assertRaises(ValueError):plan_member_selection(r,'11')

    def test_portuguese_yesterday_today_time_status_is_exact(self):
        for status in ('visto ontem às 6:12','visto hoje às 00:00','visto ontem às 23:59'):
            r=portuguese_sample();r['backup_ocr']['detections'][1]['text']=status
            self.assertEqual(plan_member_selection(r,'11')['action'],'click_once')
        for status in ('visto ontem às 24:12','visto ontem às 6:60',
                'ontem às 6:12','visto ontem às 6:12 extra','visto ontem as 6:12'):
            r=portuguese_sample();r['backup_ocr']['detections'][1]['text']=status
            with self.assertRaises(ValueError):plan_member_selection(r,'11')

    def test_portuguese_status_date_and_similar_names_cannot_become_11(self):
        for name in ('1','011','111','11.','I1','11 H'):
            r=portuguese_sample();r['backup_ocr']['detections'][0]['text']=name
            self.assertEqual(analyze_member_visual(r,'11')['list_matches'],0,name)
        r=portuguese_sample();r['backup_ocr']['detections'][1]['text']='visto em 2026/11/11'
        self.assertEqual(analyze_member_visual(r,'11')['list_matches'],1)
        r['backup_ocr']['detections'].pop(0)
        self.assertEqual(analyze_member_visual(r,'11')['list_matches'],0)

    def test_portuguese_wrong_status_geometry_and_confidence_still_stop(self):
        for kind in ('unaligned','far','unknown','low_status','low_name','avatar','binding'):
            r=portuguese_sample();name,status=r['backup_ocr']['detections']
            if kind=='unaligned':status['box']=detection('',(240,266,477,296))['box']
            if kind=='far':status['box']=detection('',(142,320,377,350))['box']
            if kind=='unknown':status['text']='outro texto 11'
            if kind=='low_status':status['score']=.8
            if kind=='low_name':name['score']=.8
            if kind=='avatar':name['box']=detection('',(33,223,80,257))['box']
            if kind=='binding':r['dialog_runtime_id']='changed'
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):plan_member_selection(r,'11')

    def test_portuguese_duplicate_status_aligned_names_stop(self):
        r=portuguese_sample()
        r['regions']['list']['height']=244;bind(r)
        r['backup_ocr']['detections']+=[detection('11',(142,337,180,371)),
            detection('visto recentemente',(142,380,377,410))]
        self.assertEqual(analyze_member_visual(r,'11')['list_matches'],2)
        with self.assertRaises(ValueError):plan_member_selection(r,'11')

    def test_portuguese_same_row_seen_by_both_engines_counts_once(self):
        r=portuguese_sample()
        r['ocr']['words'].append({'text':'11','left':147,'top':227,'width':27,'height':24})
        self.assertEqual(analyze_member_visual(r,'11')['list_matches'],1)
        self.assertEqual(plan_member_selection(r,'11')['action'],'click_once')



if __name__=='__main__':unittest.main()
