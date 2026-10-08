"""Clipped A header, full 20-chip viewport and interruption regressions."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backup_ocr import FRAME_KEYS, inspect_selection_visual
from controls_probe import select_member_test, WindowActionError
from selection_header_scan import (BIND_KEYS,bind_page,page_area,validate_full_header,read_current_header,geometry,validate_header_point,verify_native_surface_files)
from visual_members import analyze_member_visual,member_header_guard,plan_member_selection

WINDOW={'hwnd':66934,'pid':4580,'path':r'C:\test\Telegram.exe'}


def page(offset,labels=tuple(str(n) for n in range(24,44)),height=244):
    # Sanitized native viewport: 364 x 104, independent from the long peer list.
    r={'ok':True,'read_only':True,'scope':'member_visual','scale':2,
        'window_handle':66934,'process_id':4580,'dialog_runtime_id':'bound-members',
        'image_path':'test.png','final_invite_clicked':False,
        'capture':{'left':748,'top':218,'width':364,'height':580},
        'button_regions':{'add':{'left':1045,'top':754,'width':57,'height':34},
            'cancel':{'left':964,'top':754,'width':75,'height':34}},
        'regions':{'header':{'left':748,'top':266,'width':364,'height':104},
            'search':{'left':758,'top':266-offset+height-31,'width':344,'height':25},
            'list':{'left':748,'top':370,'width':364,'height':7188},
            'viewport':{'left':748,'top':370,'width':364,'height':374}},
        'ocr':{'available':True,'text':'Add Members Cancel Add','words':[]},
        'header_scroll':{'inner_runtime_id':'header','viewport_runtime_id':'head-view','scroll_runtime_id':'head-scroll',
            'search_runtime_id':'head-search','search_value':'',
            'viewport':{'left':748,'top':266,'width':364,'height':104},
            'inner':{'left':748,'top':266-offset,'width':364,'height':height},'offset':offset,'max_offset':height-104}}
    r['regions']['header_ocr']=page_area(r);area=r['regions']['header_ocr']
    words=[]
    for i,label in enumerate(labels):
        x=790+(i%4)*72;y=266-offset+13+(i//4)*40
        if area['top']<=y and y+16<=area['top']+area['height']:
            left=(x-748)*2;top=(y-218)*2
            words.append({'text':label,'score':.999,'pale_gray_border':1,'dark_ink_fraction':.05,
                'box':[[left,top],[left+36,top],[left+36,top+32],[left,top+32]]})
    backup={'ok':True,'accepted':words,'rejected':[],'selected_numbers':[w['text'] for w in words],
        'other_header_words':[],'image_sha256':'a'*64,'final_invite_clicked':False,'detections':[]}
    return bind_page(r,backup,'a'*64)


def point_proof(report,offset,target='inner',walker='raw'):
    state=report['header_scroll'];view=copy.deepcopy(state['viewport']);search=copy.deepcopy(report['regions']['search'])
    search['top']+=state['offset']-offset
    runtime={'inner':'inner_runtime_id','viewport':'viewport_runtime_id','scroll':'scroll_runtime_id'}[target]
    cls={'inner':'class Ui::MultiSelect::Inner','viewport':'class QWidget','scroll':'class Ui::ScrollArea'}[target]
    bounds=copy.deepcopy(view)
    if target=='inner':bounds['top']-=offset;bounds['height']+=state['max_offset']
    return {'x':930,'y':318,'offset':offset,'viewport':view,'search':search,'verified':True,'search_excluded':True,
        'bound_runtime_id':state[runtime],'bound_class':cls,
        'paths':[{'walker':walker,'nodes':[{'runtime_id':state[runtime],'class_name':cls,
            'process_id':report['process_id'],'bounds':bounds}]}]}


def modal_proof(report,offset):
    p=point_proof(report,offset)
    p.update(mode='qt_modal_surface',geometry_bound=True,bound_runtime_id='head-view',bound_class='class QWidget')
    controls=[{'runtime_id':rid,'class_name':cls} for rid,cls in (
        ('history','class HistoryInner'),('elastic','class Ui::ElasticScroll'),('history-widget','class HistoryWidget'))]
    nodes=[{**c,'process_id':report['process_id'],'bounds':copy.deepcopy(report['capture'])} for c in controls]
    nodes.append({'runtime_id':'test-main','class_name':'class MainWindow','process_id':report['process_id'],
        'bounds':copy.deepcopy(report['capture'])})
    p['qt_history_binding']={'binding_method':'history_ancestors','main_runtime_id':'test-main','controls':controls,
        'ancestor_nodes':[{k:n[k] for k in ('runtime_id','class_name','process_id')} for n in nodes]}
    p['paths']=[{'walker':'raw','nodes':nodes}]
    state=copy.deepcopy(report['header_scroll']);state['offset']=offset
    state['inner']['top']=state['viewport']['top']-offset
    p['surface']={'ok':True,'attempt_index':1,'foreground_verified':True,'compared_pixels':100000,'different_pixels':0,
        'reference_sha256':'a'*64,'screen_sha256':'b'*64,'reference_image_path':'reference.png','screen_image_path':'screen.png',
        'frame':{**{k:copy.deepcopy(report[k]) for k in BIND_KEYS[:4]},'header_scroll':state},
        'header':copy.deepcopy(report['regions']['header']),'search':copy.deepcopy(p['search'])}
    p['surface_attempts']=[copy.deepcopy(p['surface'])]
    return p


def fixture(labels=tuple(str(n) for n in range(24,44)),height=244):
    maximum=height-104;offsets=list(range(0,maximum,40))+[maximum]
    pages=[page(offset,labels,height) for offset in offsets]
    r=page(maximum,labels,height)
    events=[]
    # Records two complete passes: up, down, up, down. Input is only wheel.
    positions=[maximum,0]+offsets[1:]+[0]+offsets[1:]
    for before,after in zip(positions,positions[1:]):
        events.append({'index':len(events)+1,'requested':True,'sent':True,'binding_verified':True,
            'viewport_runtime_id':'head-view','delta':40 if after<before else -40,
            'x':930,'y':318,'before_offset':before,'after_offset':after,'pointer_parked':True,
            'point_proof':point_proof(r,before)})
    native={'ok':True,'scope':'member_header_scroll_scan','process_path_verified':True,'header_binding_verified':True,
        'executable_path':WINDOW['path'],'state':'captured','window_handle':66934,'process_id':4580,
        'dialog_runtime_id':'bound-members','wheel_events':events,'point_guard_version':4,
        'search_changed':False,'selection_click_sent':False,'final_invite_clicked':False,'database_updated':False}
    r['full_header_scan']={'ok':True,'scope':'full_current_header','window':copy.deepcopy(WINDOW),'native':native,
        'passes':[copy.deepcopy(pages),copy.deepcopy(pages)],
        'final_frame':{k:copy.deepcopy(r[k]) for k in BIND_KEYS+('regions','header_scroll')},'final_image_sha256':'a'*64,
        'search_changed':False,'selection_click_sent':False,'final_invite_clicked':False,'database_updated':False}
    return r


class FullHeaderTests(unittest.TestCase):
    def test_raw_hit_inner_is_bound_even_when_controlview_skips_viewport(self):
        r=fixture();p=point_proof(r,20)
        p['paths'].insert(0,{'walker':'control','nodes':[{'runtime_id':'dialog-parent',
            'class_name':'class PeerListBox','process_id':4580,'bounds':r['capture']}]})
        self.assertTrue(validate_header_point(p,r))

    def test_exact_header_viewport_or_scroll_container_is_supported(self):
        r=fixture()
        for target in ('inner','viewport','scroll'):
            for walker in ('raw','control'):
                self.assertTrue(validate_header_point(point_proof(r,20,target,walker),r))

    def test_hit_modal_root_peer_list_or_other_header_never_allows_wheel(self):
        r=fixture()
        for target,cls in [('dialog-parent','class PeerListBox'),('main-window','class MainWindow'),
                ('contact-list','class PeerListContent'),('other-header','class Ui::MultiSelect::Inner')]:
            p=point_proof(r,20);p.update(bound_runtime_id=target,bound_class=cls)
            node=p['paths'][0]['nodes'][0];node.update(runtime_id=target,class_name=cls)
            with self.assertRaises(ValueError):validate_header_point(p,r)

    def test_hit_search_or_outside_clipped_header_never_allows_wheel(self):
        r=fixture()
        for x,y in [(930,345),(930,400),(930,264),(747,300)]:
            p=point_proof(r,140);p.update(x=x,y=y)
            with self.assertRaises(ValueError):validate_header_point(p,r)

    def test_hit_path_pid_bounds_and_result_flags_are_required(self):
        r=fixture()
        for kind in ('pid','bounds','verified','search','class','empty','wrong_walker','layout'):
            p=point_proof(r,20)
            if kind=='pid':p['paths'][0]['nodes'][0]['process_id']=999
            if kind=='bounds':p['paths'][0]['nodes'][0]['bounds']['top']+=1
            if kind=='verified':p['verified']=False
            if kind=='search':p['search_excluded']=False
            if kind=='class':p['paths'][0]['nodes'][0]['class_name']='class PeerListContent'
            if kind=='empty':p['paths'][0]['nodes']=[]
            if kind=='wrong_walker':p['paths'][0]['walker']='unbound'
            if kind=='layout':p['search']['top']+=1
            with self.assertRaises(ValueError,msg=kind):validate_header_point(p,r)

    def test_hit_foreign_child_cannot_be_masked_by_correct_ancestor(self):
        r=fixture();p=point_proof(r,20)
        p['paths'][0]['nodes'].insert(0,{'runtime_id':'covered','class_name':'class QWidget',
            'process_id':999,'bounds':r['capture']})
        with self.assertRaises(ValueError):validate_header_point(p,r)

    def test_scroll_event_must_bind_actual_point_and_park_before_ocr(self):
        for kind in ('missing','mismatch','park','version'):
            r=fixture();event=r['full_header_scan']['native']['wheel_events'][0]
            if kind=='missing':event.pop('point_proof')
            if kind=='mismatch':event['point_proof']['x']+=1
            if kind=='park':event['pointer_parked']=False
            if kind=='version':r['full_header_scan']['native']['point_guard_version']=1
            with self.assertRaises(ValueError,msg=kind):validate_full_header(r)

    def test_observed_history_hit_requires_bound_modal_and_visible_screen_confirmation(self):
        r=fixture();p=modal_proof(r,20)
        self.assertTrue(validate_header_point(p,r))
        p.pop('surface')
        with self.assertRaises(ValueError):validate_header_point(p,r)

    def test_reported_qt_history_branch_without_surface_is_still_rejected(self):
        r=fixture();p=modal_proof(r,20)
        p['verified']=False
        with self.assertRaises(ValueError):validate_header_point(p,r)
        p['verified']=True;p['geometry_bound']=False
        with self.assertRaises(ValueError):validate_header_point(p,r)

    def test_screen_mismatch_insufficient_pixels_or_missing_hash_cannot_allow_wheel(self):
        r=fixture()
        for kind in ('mismatch','few','too_many','negative','failed','foreground','hash'):
            p=modal_proof(r,20);v=p['surface']
            if kind=='mismatch':v['different_pixels']=501
            if kind=='few':v['compared_pixels']=999
            if kind=='too_many':v['compared_pixels']=99999999
            if kind=='negative':v['different_pixels']=-1
            if kind=='failed':v['ok']=False
            if kind=='foreground':v['foreground_verified']=False
            if kind=='hash':v['screen_sha256']='missing'
            with self.assertRaises(ValueError,msg=kind):validate_header_point(p,r)

    def test_surface_other_window_dialog_offset_or_header_is_rejected(self):
        r=fixture()
        for kind in ('window','dialog','offset','height','viewport','search','runtime'):
            p=modal_proof(r,20);frame=p['surface']['frame']
            if kind=='window':frame['window_handle']=999
            if kind=='dialog':frame['dialog_runtime_id']='other-dialog'
            if kind=='offset':frame['header_scroll']['offset']=19
            if kind=='height':frame['header_scroll']['inner']['height']+=1
            if kind=='viewport':p['surface']['header']['top']+=1
            if kind=='search':frame['header_scroll']['search_value']='other query'
            if kind=='runtime':frame['header_scroll']['viewport_runtime_id']='other-header'
            with self.assertRaises(ValueError,msg=kind):validate_header_point(p,r)

    def test_arbitrary_hit_branch_wrong_root_or_pid_cannot_use_modal_surface_mode(self):
        r=fixture()
        for kind in ('class','history_id','root','pid','missing','control_only'):
            p=modal_proof(r,20);nodes=p['paths'][0]['nodes']
            if kind=='class':nodes[0]['class_name']='class PeerListContent'
            if kind=='history_id':nodes[0]['runtime_id']='other-history'
            if kind=='root':nodes[-1]['runtime_id']='other-main'
            if kind=='pid':nodes[1]['process_id']=999
            if kind=='missing':p['qt_history_binding']['controls'].pop()
            if kind=='control_only':p['paths'][0]['walker']='control'
            with self.assertRaises(ValueError,msg=kind):validate_header_point(p,r)

    def test_full_twenty_selection_uses_surface_evidence_for_every_wheel(self):
        r=fixture();native=r['full_header_scan']['native']
        native['qt_history_binding']=modal_proof(r,20)['qt_history_binding']
        for event in native['wheel_events']:event['point_proof']=modal_proof(r,event['before_offset'])
        self.assertEqual(validate_full_header(r)[0],[str(n) for n in range(24,44)])
        native['wheel_events'][0]['point_proof']['qt_history_binding']['main_runtime_id']='other-main'
        with self.assertRaises(ValueError):validate_full_header(r)

    def test_observed_parent_chain_accepts_wrappers_but_stops_at_same_window(self):
        r=fixture();p=modal_proof(r,20)
        wrapper={'runtime_id':'main-widget','class_name':'class MainWidget','process_id':r['process_id']}
        p['qt_history_binding']['ancestor_nodes'].insert(3,wrapper)
        p['paths'][0]['nodes'].insert(3,{**wrapper,'bounds':copy.deepcopy(r['capture'])})
        # The desktop above the bound root belongs to a different process.
        p['paths'][0]['nodes'].append({'runtime_id':'desktop','class_name':'Desktop','process_id':999})
        self.assertTrue(validate_header_point(p,r))

    def test_sibling_elastic_scroll_cannot_replace_history_actual_parent(self):
        r=fixture()
        for changed in ('controls','ancestor_nodes','both'):
            p=modal_proof(r,20);binding=p['qt_history_binding']
            if changed in ('controls','both'):binding['controls'][1]['runtime_id']='another-elastic'
            if changed in ('ancestor_nodes','both'):binding['ancestor_nodes'][1]['runtime_id']='another-elastic'
            with self.assertRaises(ValueError,msg=changed):validate_header_point(p,r)

    def test_strict_surface_can_settle_before_wheel_with_all_captures_recorded(self):
        r=fixture();p=modal_proof(r,20)
        first=copy.deepcopy(p['surface']);first.update(ok=False,different_pixels=1315,
            reference_image_path='first-reference.png',screen_image_path='first-screen.png')
        p['surface']['attempt_index']=2
        p['surface_attempts']=[first,copy.deepcopy(p['surface'])]
        self.assertTrue(validate_header_point(p,r))
        # A persistent mismatch never becomes proof of a safe wheel.
        p['surface']['different_pixels']=1315;p['surface_attempts'][-1]=copy.deepcopy(p['surface'])
        with self.assertRaises(ValueError):validate_header_point(p,r)

    def test_surface_settling_cannot_change_binding_hide_attempts_or_exceed_limit(self):
        r=fixture()
        for kind in ('missing','empty','over_limit','sequence','frame','search','foreground','early_ok','hash','path','final'):
            p=modal_proof(r,20);a=p['surface_attempts'][0]
            if kind=='missing':p.pop('surface_attempts')
            if kind=='empty':p['surface_attempts']=[]
            if kind=='over_limit':p['surface_attempts']*=4
            if kind=='sequence':a['attempt_index']=2
            if kind=='frame':a['frame']['dialog_runtime_id']='other-modal'
            if kind=='search':a['search']['top']+=1
            if kind=='foreground':a['foreground_verified']=False
            if kind=='early_ok':p['surface_attempts'].insert(0,copy.deepcopy(a))
            if kind=='hash':a['screen_sha256']='invalid'
            if kind=='path':a['reference_image_path']=a['screen_image_path']
            if kind=='final':a['different_pixels']=1
            with self.assertRaises(ValueError,msg=kind):validate_header_point(p,r)

    def test_missing_cross_process_repeated_or_wrong_root_parent_chain_is_rejected(self):
        r=fixture()
        for kind in ('method','missing','pid','repeat','root','class','wrapper'):
            p=modal_proof(r,20);binding=p['qt_history_binding'];ancestors=binding['ancestor_nodes']
            if kind=='method':binding['binding_method']='global_class_search'
            if kind=='missing':binding.pop('ancestor_nodes')
            if kind=='pid':ancestors[2]['process_id']=999
            if kind=='repeat':ancestors[-1]['runtime_id']=ancestors[0]['runtime_id']
            if kind=='root':ancestors[-1]['runtime_id']='other-main'
            if kind=='class':ancestors[-1]['class_name']='class QWidget'
            if kind=='wrapper':ancestors.insert(3,{'runtime_id':'unobserved','class_name':'class MainWidget','process_id':r['process_id']})
            with self.assertRaises(ValueError,msg=kind):validate_header_point(p,r)

    def test_fresh_surface_file_hashes_are_bound_and_modified_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'reference.png';b=Path(tmp)/'screen.png';a.write_bytes(b'window');b.write_bytes(b'screen')
            surface={'ok':True,'reference_image_path':str(a),'screen_image_path':str(b),
                'reference_sha256':hashlib.sha256(a.read_bytes()).hexdigest(),'screen_sha256':hashlib.sha256(b.read_bytes()).hexdigest()}
            native={'surface_checks':[{'surface':surface}]}
            verify_native_surface_files(native)
            b.write_bytes(b'changed')
            with self.assertRaises(ValueError):verify_native_surface_files(native)
            b.unlink()
            with self.assertRaises(OSError):verify_native_surface_files(native)

    def test_settling_keeps_failed_capture_hashes_and_requires_final_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            surfaces=[]
            for index in range(2):
                a=Path(tmp)/('reference'+str(index)+'.png');b=Path(tmp)/('screen'+str(index)+'.png')
                a.write_bytes(b'reference');b.write_bytes(b'screen')
                surfaces.append({'ok':index==1,'reference_image_path':str(a),'screen_image_path':str(b),
                    'reference_sha256':hashlib.sha256(a.read_bytes()).hexdigest(),'screen_sha256':hashlib.sha256(b.read_bytes()).hexdigest()})
            native={'surface_checks':[{'surface':surfaces[-1],'surface_attempts':surfaces}]}
            verify_native_surface_files(native)
            Path(surfaces[0]['screen_image_path']).write_bytes(b'changed')
            with self.assertRaises(ValueError):verify_native_surface_files(native)
            surfaces[-1]['ok']=False
            with self.assertRaises(ValueError):verify_native_surface_files(native)

    def test_twenty_labels_are_read_from_current_pages_without_history(self):
        r=fixture();numbers,other=validate_full_header(r)
        self.assertEqual(numbers,[str(n) for n in range(24,44)]);self.assertEqual(other,[])
        self.assertTrue(analyze_member_visual(r,'31')['usable'])
        self.assertEqual(analyze_member_visual(r,'31')['chip_matches'],1)
        # Final capture only shows last rows; caret protection stays within that frame.
        guard=member_header_guard(r)
        self.assertLess(guard['selected_label_count'],20)
        self.assertEqual(guard['selected_label_count'],len(r['backup_ocr']['accepted']))

    def test_clipped_eight_chip_native_shape_is_fully_read(self):
        r=fixture(tuple(str(n) for n in range(24,32)),124)
        self.assertEqual(validate_full_header(r)[0],[str(n) for n in range(24,32)])
        r.pop('full_header_scan')
        self.assertFalse(analyze_member_visual(r,'31')['usable'])

    def test_same_label_on_different_positions_is_not_deduplicated(self):
        labels=list(str(n) for n in range(24,44));labels[4]='24'
        r=fixture(labels);numbers,_=validate_full_header(r)
        self.assertEqual(numbers.count('24'),2)
        with self.assertRaises(ValueError):plan_member_selection(r,'50',numbers)

    def test_overlapping_position_cannot_change_label(self):
        r=fixture();p=r['full_header_scan']['passes'][0][1]
        w=p['backup_ocr']['accepted'][0];w['text']='99'
        p['backup_ocr']['selected_numbers'][0]='99'
        with self.assertRaisesRegex(ValueError,'冲突'):validate_full_header(r)

    def single_down_fixture(self):
        r=fixture();p=r['full_header_scan'];n=p['native']
        p['passes']=p['passes'][:1];p['scan_pass_count']=n['scan_pass_count']=1
        n['scan_mode']='single_downward_page_scan';n['wheel_events']=n['wheel_events'][:5]
        for index,e in enumerate(n['wheel_events']):
            e['phase']='position_top' if index==0 else 'read_down';e['delta']=1200 if index==0 else -120
        return r

    def test_single_direction_scan_complete_and_bound(self):
        self.assertEqual(validate_full_header(self.single_down_fixture())[0],[str(n) for n in range(24,44)])

    def test_single_direction_scan_rejects_upward_repeat(self):
        r=self.single_down_fixture();n=r['full_header_scan']['native']
        e=copy.deepcopy(n['wheel_events'][0]);e['index']=6;n['wheel_events'].append(e)
        with self.assertRaises(ValueError):validate_full_header(r)

    def test_single_direction_scan_rejects_excessive_jump_or_wrong_phase(self):
        for mode in ('jump','phase'):
            r=self.single_down_fixture();e=r['full_header_scan']['native']['wheel_events'][1]
            if mode=='jump':e['after_offset']=70
            else:e['phase']='position_top'
            with self.assertRaises(ValueError,msg=mode):validate_full_header(r)

    def test_single_direction_scan_rejects_second_pass(self):
        r=self.single_down_fixture();p=r['full_header_scan'];p['passes'].append(copy.deepcopy(p['passes'][0]))
        p['scan_pass_count']=p['native']['scan_pass_count']=2
        with self.assertRaises(ValueError):validate_full_header(r)

    def test_two_complete_passes_must_agree(self):
        r=fixture();p=r['full_header_scan']['passes'][1][-1]
        w=p['backup_ocr']['accepted'][-1];w['text']='99';p['backup_ocr']['selected_numbers'][-1]='99'
        with self.assertRaises(ValueError):validate_full_header(r)

    def test_missing_top_bottom_or_insufficient_overlap_is_rejected(self):
        for kind in ('top','bottom','gap','reverse'):
            r=fixture();ps=r['full_header_scan']['passes'][0]
            if kind=='top':ps.pop(0)
            if kind=='bottom':ps.pop()
            if kind=='gap':ps.pop(1)
            if kind=='reverse':ps[1],ps[2]=ps[2],ps[1]
            with self.assertRaises(ValueError,msg=kind):validate_full_header(r)

    def test_frame_window_search_height_or_digest_change_is_rejected(self):
        for kind in ('window','dialog','search','height','viewport','hash','area'):
            r=fixture();p=r['full_header_scan']['passes'][1][1]
            if kind=='window':p['window_handle']=1
            if kind=='dialog':p['dialog_runtime_id']='different'
            if kind=='search':p['header_scroll']['search_value']='31'
            if kind=='height':p['header_scroll']['inner']['height']+=40
            if kind=='viewport':p['header_scroll']['viewport']['top']+=1
            if kind=='hash':p['selection_header_ocr']['image_sha256']='b'*64
            if kind=='area':p['regions']['header_ocr']['top']-=10
            with self.assertRaises(ValueError,msg=kind):validate_full_header(r)

    def test_low_confidence_other_names_and_clipped_glyphs_are_not_inferred(self):
        r=fixture();p=r['full_header_scan']['passes'][0][0]
        p['backup_ocr']['accepted'][0]['score']=.79
        with self.assertRaises(ValueError):validate_full_header(r)
        r=fixture(['O']+[str(n) for n in range(25,44)])
        # Non-numeric visible identity remains present and stops the planner.
        for group in r['full_header_scan']['passes']:
            for p in group:
                p['backup_ocr']['other_header_words']=[w['text'] for w in p['backup_ocr']['accepted'] if w['text']=='O']
                p['backup_ocr']['selected_numbers']=[w['text'] for w in p['backup_ocr']['accepted'] if w['text']!='O']
        self.assertEqual(validate_full_header(r)[1],['O'])
        with self.assertRaises(ValueError):plan_member_selection(r,'50')

    def test_wheel_binding_input_state_and_final_frame_must_be_complete(self):
        for kind in ('sent','point','viewport','invited','window','final','proof'):
            r=fixture();proof=r['full_header_scan'];event=proof['native']['wheel_events'][0]
            if kind=='sent':event['sent']=False
            if kind=='point':event['y']=500
            if kind=='viewport':event['viewport_runtime_id']='peer-list'
            if kind=='invited':proof['native']['final_invite_clicked']=True
            if kind=='window':proof['window']['pid']=999
            if kind=='final':r['header_scroll']['offset']-=1
            if kind=='proof':proof['final_frame']['regions']['header']['top']-=1
            with self.assertRaises(ValueError,msg=kind):validate_full_header(r)

    def native_and_worker(self,tmp,corrupt=None):
        r=fixture();proof=r['full_header_scan']
        native=copy.deepcopy(proof['native'])
        native['passes']=[{'index':i,'pages':copy.deepcopy(pages)} for i,pages in enumerate(proof['passes'],1)]
        native['final']=copy.deepcopy(r);native['final'].pop('full_header_scan')
        pages=[p for g in native['passes'] for p in g['pages']]+[native['final']]
        backup=[]
        for index,p in enumerate(pages):
            path=Path(tmp)/f'page{index}.png';path.write_bytes(f'capture-{index}'.encode())
            p['image_path']=str(path);b=p.pop('backup_ocr');p.pop('selection_header_ocr');p.pop('header_page')
            p['regions'].pop('header_ocr');b['image_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            backup.append(b)
        if corrupt=='hash':backup[0]['image_sha256']='b'*64
        def worker(_):
            if corrupt=='file':Path(pages[0]['image_path']).write_bytes(b'replaced')
            return {'ok':True,'pages':backup,'final_invite_clicked':False}
        return native,worker

    def test_live_pipeline_binds_every_page_and_rejects_changed_files(self):
        for corrupt in (None,'hash','file'):
            with tempfile.TemporaryDirectory() as tmp:
                source=page(140);source['image_path']=str(Path(tmp)/'source.png')
                native,worker=self.native_and_worker(tmp,corrupt)
                with patch('backup_ocr.require_environment'),\
                     patch('controls_probe.run_window_script',return_value=native) as action,\
                     patch('backup_ocr.run_backup_ocr',side_effect=worker):
                    result=read_current_header(WINDOW,source)
                self.assertEqual(result['full_header_scan']['ok'],corrupt is None)
                action.assert_called_once()
                self.assertEqual(action.call_args.args[1],'scroll_member_header.ps1')
                if corrupt is None:
                    self.assertEqual(result['analysis']['selected_numbers'],[str(n) for n in range(24,44)])
                    self.assertEqual(Path(result['image_path']).name,'page10.png')
                else:self.assertFalse(analyze_member_visual(result,'31')['usable'])

    def test_native_failure_is_retained_without_ocr_or_selection(self):
        source=page(140)
        error={'ok':False,'selection_click_sent':False,'final_invite_clicked':False,'state':'review'}
        with patch('backup_ocr.require_environment'),\
             patch('controls_probe.run_window_script',side_effect=WindowActionError('wheel failed',error)) as action,\
             patch('backup_ocr.run_backup_ocr') as worker:
            result=read_current_header(WINDOW,source)
        action.assert_called_once();worker.assert_not_called()
        self.assertFalse(result['full_header_scan']['ok']);self.assertEqual(result['full_header_scan']['native_error'],error)

    def test_missing_environment_stops_before_any_native_scroll(self):
        with patch('backup_ocr.require_environment',side_effect=ValueError('missing environment')),\
             patch('controls_probe.run_window_script') as action:
            r=read_current_header(WINDOW,page(140))
        action.assert_not_called();self.assertFalse(r['full_header_scan']['ok'])

    def test_after_eight_selected_next_contact_clicks_once_with_fresh_bottom_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            before=fixture(tuple(str(n) for n in range(24,32)),124)
            after=fixture(tuple(str(n) for n in range(24,33)),164)
            # The next exact name exists only in the contact list, not among chips.
            before['ocr']['words']=[{'text':'32','left':140,'top':304,'width':38,'height':33}]
            path=Path(tmp)/'fresh_bottom.png';path.write_bytes(b'fresh bottom capture')
            before['image_path']=str(path);events=[]
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            before['backup_ocr']['image_sha256']=digest
            before['selection_header_ocr']['image_sha256']=digest
            before['full_header_scan']['final_image_sha256']=digest
            def native(window,script,payload):
                mode=payload.get('mode');events.append(mode)
                if mode=='prepare':
                    Path(payload['image_path']).write_bytes(b'old partial capture')
                    r=page(20,tuple(str(n) for n in range(24,32)),124)
                    r['search_preparation']={'number':'32','value_matches':True};return r
                if mode=='check':
                    self.assertEqual(payload['before_image_path'],str(path))
                    self.assertEqual(payload['before_sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
                    return {'ok':True,'mode':'check','read_only':True,'guard_stable':True,
                        'click_attempted':False,'click_sent':False,'final_invite_clicked':False,
                        'number':'32','before_sha256':payload['before_sha256']}
                if mode=='click_once':
                    self.assertEqual(payload['before_image_path'],str(path))
                    return {'ok':True,'click_sent':True,'final_invite_clicked':False}
                return page(60,tuple(str(n) for n in range(24,33)),164)
            reads=0
            def reader(r):
                nonlocal reads
                reads+=1
                return copy.deepcopy(before if reads==1 else after)
            with patch('controls_probe.run_window_script',side_effect=native),patch('controls_probe.time.sleep'):
                r=select_member_test(WINDOW,Path(tmp)/'step32.png','32',
                    expected_selected=tuple(str(n) for n in range(24,32)),header_reader=reader)
            self.assertTrue(r['selection_verified'],r.get('reason'))
            self.assertEqual(events,['prepare','check','click_once',None,None,None])
            self.assertEqual(r['analysis']['selected_numbers'],[str(n) for n in range(24,33)])
            self.assertFalse(r['final_invite_clicked'])

    def test_initial_and_final_batch_full_header_read_keeps_all_twenty_without_toggle(self):
        from member_batch import select_members_test
        r=fixture();r['analysis']=analyze_member_visual(r,'24')
        with tempfile.TemporaryDirectory() as tmp:
            with patch('member_batch.inspect_member_visual',return_value=r) as capture,\
                 patch('member_batch.select_member_test') as click,patch('member_batch.time.sleep'):
                result=select_members_test(WINDOW,Path(tmp)/'all.json',[str(n) for n in range(24,44)])
            self.assertTrue(result['selection_verified'],result.get('reason'))
            self.assertEqual(capture.call_count,6);click.assert_not_called()
            self.assertEqual(result['remaining_numbers'],[])
            self.assertFalse(result['final_invite_clicked']);self.assertFalse(result['database_updated'])

    def test_legacy_visible_header_does_not_scroll(self):
        from test_member_selection import live_report
        r=live_report()
        with patch('backup_ocr.apply_selection_header',return_value=r) as reader,\
             patch('controls_probe.run_window_script') as native:
            self.assertIs(read_current_header({},r),r)
        reader.assert_called_once();native.assert_not_called()


if __name__=='__main__':unittest.main()
