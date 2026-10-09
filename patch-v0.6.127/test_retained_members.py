"""B's Desktop keeps the chosen row after clearing the search query."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backup_ocr import FRAME_KEYS
from controls_probe import select_member_test
from member_batch import select_members_test
from visual_members import analyze_member_visual, plan_member_selection, verify_member_selection


def frame(selected=(),target=None,full_list=False):
    # Sanitized geometry from B's failed post-click report. Rows persist and
    # list content height changes from 76 to 1588 after the query clears.
    r={'ok':True,'read_only':True,'scope':'member_visual','scale':2,
        'window_handle':100,'process_id':200,'dialog_runtime_id':'test-b-dialog',
        'final_invite_clicked':False,'image_path':'test.png',
        'capture':{'left':994,'top':234,'width':364,'height':580},
        'regions':{'list':{'left':994,'top':326,'width':364,'height':1588 if full_list else 76},
            'viewport':{'left':994,'top':326,'width':364,'height':434},
            'header':{'left':994,'top':282,'width':364,'height':44},
            'search':{'left':1040+29*len(selected),'top':295,'width':260,'height':25}},
        'button_regions':{'add':{'left':1253,'top':770,'width':95,'height':34},
            'cancel':{'left':1158,'top':770,'width':89,'height':34}},
        'ocr':{'available':True,'text':'Adicionar Membros Cancelar Adicionar','words':[]},
        'backup_ocr':{'ok':True,'image_sha256':'a'*64,'final_invite_clicked':False,
            'accepted':[],'rejected':[],'selected_numbers':list(selected),'other_header_words':[]}}
    if full_list:
        rows=[('3',148,231),('4',147,343)]
    else:rows=[] if target is None else [(target,148,231)]
    r['ocr']['words']=[{'text':label,'left':left,'top':top,'width':13,'height':19} for label,left,top in rows]
    for i,label in enumerate(selected):
        left=86+58*i
        r['backup_ocr']['accepted'].append({'text':label,'score':.99998,
            'pale_gray_border':1.0,'dark_ink_fraction':.0375,
            'box':[[left,125],[left+24,125],[left+24,155],[left,155]]})
    bind(r)
    if target:r['search_preparation']={'number':target,'value_matches':True,'search_attempted':True}
    return r


def bind(r):
    r['selection_header_ocr']={'ok':True,'used_for_selection':True,'image_sha256':'a'*64,
        'frame':{key:copy.deepcopy(r[key]) for key in FRAME_KEYS}}


class RetainedMemberTests(unittest.TestCase):
    def test_b_three_new_chip_and_retained_unique_row_is_verified(self):
        before=frame(target='3');after=frame(['3'],full_list=True)
        result=verify_member_selection(before,after,'3')
        self.assertTrue(result['selection_verified'])
        self.assertEqual(result['analysis']['list_matches'],1)
        self.assertFalse(result['final_invite_clicked'])

    def test_four_can_move_from_first_filtered_row_to_second_full_list_row(self):
        before=frame(['3'],target='4');after=frame(['3','4'],full_list=True)
        self.assertTrue(verify_member_selection(before,after,'4',['3'])['selection_verified'])

    def test_retained_preselected_row_never_toggles_selected_member(self):
        self.assertEqual(plan_member_selection(frame(['3'],full_list=True),'3')['action'],'already_selected')

    def test_new_chip_still_requires_complete_previous_selection(self):
        before=frame(['3'],target='4')
        for selected in ([],['4'],['3'],['3','4','5'],['3','4','4']):
            after=frame(selected,full_list=True)
            self.assertFalse(verify_member_selection(before,after,'4',['3'])['selection_verified'],selected)

    def test_duplicate_target_rows_still_stop_verification_and_no_toggle(self):
        before=frame(target='3');after=frame(['3'],full_list=True)
        after['ocr']['words'][1]['text']='3'
        self.assertFalse(verify_member_selection(before,after,'3')['selection_verified'])
        with self.assertRaises(ValueError):plan_member_selection(after,'3')

    def test_retained_row_does_not_relax_legacy_windows_only_header_rule(self):
        before=frame(target='3');after=frame(['3'],full_list=True)
        after.pop('selection_header_ocr');after.pop('backup_ocr')
        after['ocr']['words'].append({'text':'3','left':92,'top':131,'width':13,'height':19})
        self.assertFalse(verify_member_selection(before,after,'3')['selection_verified'])

    def test_wrong_dialog_invalid_binding_or_low_confidence_chip_still_fail(self):
        for kind in ('dialog','hash','gray','score','invited'):
            before=frame(target='3');after=frame(['3'],full_list=True)
            if kind=='dialog':after['dialog_runtime_id']='changed'
            if kind=='hash':after['selection_header_ocr']['image_sha256']='b'*64
            if kind=='gray':after['backup_ocr']['accepted'][0]['pale_gray_border']=.1
            if kind=='score':after['backup_ocr']['accepted'][0]['score']=.79
            if kind=='invited':after['final_invite_clicked']=True
            self.assertFalse(verify_member_selection(before,after,'3')['selection_verified'],kind)

    def test_retained_row_postchecks_repeat_reads_without_repeating_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls=[]
            def native(w,script,payload):
                calls.append(payload.get('mode'))
                if payload.get('mode')=='prepare':
                    Path(payload['image_path']).write_bytes(b'fresh capture')
                    return frame(target='3')
                if payload.get('mode')=='click_once':return {'ok':True,'click_sent':True,'final_invite_clicked':False}
                return frame(['3'],full_list=True)
            with patch('controls_probe.run_window_script',side_effect=native),patch('controls_probe.time.sleep'):
                result=select_member_test({},Path(tmp)/'test.png','3')
            self.assertTrue(result['selection_verified']);self.assertEqual(calls.count('click_once'),1)
            self.assertEqual(calls,['prepare','click_once',None,None,None])

    def test_batch_continues_three_to_four_then_checks_whole_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            events=[];initial=frame()
            initial.update(analysis=analyze_member_visual(initial,'3'),initial_stable=True,
                initial_search_applied=False,initial_reads=[])
            final=frame(['3','4'],full_list=True)
            final['analysis']=analyze_member_visual(final,'3')
            current=None
            def native(w,script,payload):
                nonlocal current
                if payload.get('mode')=='prepare':
                    current=payload['number'];events.append(('search',current))
                    Path(payload['image_path']).write_bytes(b'fresh capture')
                    return frame([] if current=='3' else ['3'],target=current)
                if payload.get('mode')=='check':
                    return {'ok':True,'mode':'check','read_only':True,'guard_stable':True,
                        'click_attempted':False,'click_sent':False,'final_invite_clicked':False,
                        'number':payload['number'],'before_sha256':payload['before_sha256']}
                if payload.get('mode')=='click_once':
                    events.append(('click',current));return {'ok':True,'click_sent':True,'final_invite_clicked':False}
                return frame(['3'] if current=='3' else ['3','4'],full_list=True)
            with patch('member_batch.inspect_initial_members',return_value=initial),\
                    patch('member_batch.inspect_member_visual',return_value=final),\
                    patch('member_batch.apply_selection_header',side_effect=lambda r:r),\
                    patch('controls_probe.run_window_script',side_effect=native),\
                    patch('controls_probe.time.sleep'),patch('member_batch.time.sleep'):
                result=select_members_test({},Path(tmp)/'batch.json',['3','4'])
            self.assertEqual(events,[('search','3'),('click','3'),('search','4'),('click','4')])
            self.assertTrue(result['selection_verified']);self.assertEqual(result['selected_numbers'],['3','4'])
            self.assertEqual(result['state'],'waiting_for_manual_invite');self.assertFalse(result['final_invite_clicked'])


if __name__=='__main__':unittest.main()
