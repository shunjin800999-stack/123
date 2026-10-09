import copy
import json
import tempfile
import unittest
import queue
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from store import Store
from contact_queue import ContactQueue, restriction_evidence, confirmed_observation


def window(n):
    return {'hwnd':100+n,'pid':200+n,'path':rf'C:\TG{n}\Telegram.exe',
            'title':f'Telegram {n}','candidate':True,'minimized':False}


def limit(w):
    return {'ok':True,'read_only':True,'scope':'dialog','scope_class':'class Ui::BoxLayerWidget',
        'scope_runtime_id':'modal-1','window_handle':w['hwnd'],'process_id':w['pid'],
        'truncated':False,'errors':[],
        'controls':[{'type':'Text','name':'Too many tries. Please try again later.','visible':True},
                    {'type':'Button','name':'OK','visible':True,'enabled':True}]}


def profile(job):
    w=job['window']
    r=limit(w)
    r.update(scope='profile',scope_class='class Info::Profile::Widget',scope_runtime_id='profile-1')
    r['controls']=[{'type':'Text','name':job['item']['value'],'visible':True},
        {'type':'Text','name':str(job['preview_number']),'visible':True},
        {'type':'Button','name':'Edit contact','visible':True,'enabled':True},
        {'type':'Button','name':'Delete contact','visible':True,'enabled':True}]
    return r


class ContactQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'progress.sqlite3'
        self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.store.import_text('phone','\n'.join('+55169912345%02d'%i for i in range(20)))

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def configure(self,targets=(20,20)):
        return self.queue.configure([{'window':window(i),'account':chr(64+i),'target':t} for i,t in enumerate(targets,1)])

    def fill(self,qid):
        job=self.queue.current(qid)
        job=self.queue.claim_fill(qid,job['id'])
        self.queue.finish_fill(qid,job['id'],{'ok':True,'mode':'fill_only','number':str(job['preview_number']),
            'phone_matches':True,'empty_guard':True,'contact_created':False,'final_invite_clicked':False})
        return self.queue.current(qid)

    def add(self,qid):
        job=self.fill(qid);r=profile(job)
        return self.queue.accept(qid,job['id'],[r,copy.deepcopy(r)])

    def test_A_target20_stops_after3_B_starts4_preserves_uncertain_person(self):
        qid=self.configure();self.queue.start(qid)
        for _ in range(3):self.assertEqual(self.add(qid)['outcome'],'added')
        failed=self.fill(qid);self.assertEqual(failed['preview_number'],4)
        r=limit(failed['window'])
        self.assertEqual(self.queue.accept(qid,failed['id'],[r,copy.deepcopy(r)])['outcome'],'restriction')
        self.assertIsNone(self.queue.current(qid))
        self.assertEqual(self.queue.snapshot(qid)['state'],'review')
        self.assertEqual(self.queue.snapshot(qid)['jobs'][1]['state'],'queued')
        self.assertEqual(self.store.next_contact_number(),4)
        self.assertEqual(self.store.addition_stats()['today_added'],3)

    def test_each_account_has_own_target_and_numbers_are_not_reserved_by_plan(self):
        qid=self.configure((2,1,3));self.queue.start(qid)
        for _ in range(6):self.add(qid)
        snap=self.queue.snapshot(qid)
        self.assertEqual([j['added_numbers'] for j in snap['jobs']],[[1,2],[3],[4,5,6]])
        self.assertEqual(snap['state'],'done');self.assertEqual(snap['next_number'],7)
        self.assertFalse(snap['final_invite_clicked'])
        self.assertTrue(all(r['status']=='added' for r in self.store.rows() if r['contact_number']))

    def test_no_silent_auto_resume_after_restart_or_replay_old_observation(self):
        qid=self.configure();self.queue.start(qid);job=self.fill(qid);r=limit(job['window'])
        self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.assertEqual(self.queue.snapshot(qid)['state'],'review')
        self.assertIsNone(self.queue.current(qid));self.assertEqual(self.store.next_contact_number(),1)
        with self.assertRaises(ValueError):self.queue.accept(qid,job['id'],[r,r])
        with self.assertRaises(ValueError):self.queue.start(qid)
        with self.assertRaises(ValueError):self.configure()

    def test_only_current_filled_item_can_accept_observation(self):
        qid=self.configure();job=self.queue.start(qid);r=limit(job['window'])
        with self.assertRaises(ValueError):self.queue.accept(qid,job['id'],[r,r])
        self.assertEqual(self.store.next_contact_number(),1)
        job=self.fill(qid);r=profile(job);self.queue.accept(qid,job['id'],[r,r])
        with self.assertRaises(ValueError):self.queue.accept(qid,job['id'],[r,r])
        self.assertEqual(self.store.next_contact_number(),2)

    def test_fill_incomplete_result_does_not_allocate_or_retry(self):
        qid=self.configure();job=self.queue.start(qid);job=self.queue.claim_fill(qid,job['id'])
        with self.assertRaises(ValueError):self.queue.finish_fill(qid,job['id'],{'ok':True})
        self.assertEqual(self.queue.snapshot(qid)['state'],'review')
        self.assertEqual(self.store.next_contact_number(),1)

    def test_stale_global_number_prevents_confirmation_and_next_account(self):
        qid=self.configure();self.queue.start(qid);job=self.fill(qid);r=profile(job)
        self.store.set_next_number(9)
        with self.assertRaisesRegex(ValueError,'全局编号'):self.queue.accept(qid,job['id'],[r,r])
        self.assertEqual(self.queue.current(qid)['account'],'A')
        self.assertEqual(self.store.addition_stats()['total_added'],0)

    def test_bad_binding_and_unresolved_account_roll_back_entire_plan(self):
        for mutation in ('window','account','target'):
            entries=[{'window':window(i),'account':chr(64+i),'target':20} for i in (1,2)]
            if mutation=='window':entries[1]['window']=window(1)
            if mutation=='account':entries[1]['account']='A'
            if mutation=='target':entries[1]['target']=41
            with self.assertRaises(ValueError):self.queue.configure(entries)
            self.assertEqual(len(self.store.batches()),0)
        b=self.store.create_batch('B');self.store.reserve_next(b)
        with self.assertRaises(ValueError):self.configure()
        self.assertEqual([b['account'] for b in self.store.batches()],['B'])
        self.assertIsNone(self.queue.latest())

    def test_false_positive_missing_or_changed_evidence_never_advances(self):
        qid=self.configure();self.queue.start(qid);job=self.fill(qid)
        for mutation in ('chat','hidden','substring','wrong_window','incomplete','errors','one_read','changed_popup','no_button','unknown'):
            r=limit(job['window']);second=copy.deepcopy(r)
            if mutation=='chat':r['scope']='window'
            if mutation=='hidden':r['controls'][0]['visible']=False
            if mutation=='substring':r['controls'][0]['name']='Quoted: '+r['controls'][0]['name']
            if mutation=='wrong_window':r['window_handle']+=1
            if mutation=='incomplete':r['truncated']=True
            if mutation=='errors':r['errors']=['unavailable']
            if mutation=='changed_popup':second['scope_runtime_id']='different'
            if mutation=='no_button':r['controls'].pop()
            if mutation=='unknown':r['controls'][0]['name']='User not found'
            reports=[r] if mutation=='one_read' else [r,second]
            self.assertIsNone(self.queue.accept(qid,job['id'],reports),mutation)
            self.assertEqual(self.queue.current(qid)['account'],'A')
            self.assertEqual(self.store.next_contact_number(),1)

    def test_profile_other_user_or_missing_saved_markers_cannot_consume_number(self):
        qid=self.configure();self.queue.start(qid);job=self.fill(qid)
        for mutation in ('phone','number','delete','edit'):
            r=profile(job)
            if mutation=='phone':r['controls'][0]['name']='+5516991234567'
            if mutation=='number':r['controls'][1]['name']='2'
            if mutation=='delete':r['controls'].pop()
            if mutation=='edit':r['controls'].pop(2)
            self.assertIsNone(self.queue.accept(qid,job['id'],[r,r]))
        self.assertEqual(self.store.addition_stats()['total_added'],0)

    def test_empty_form_guard_is_sent_and_full_summary_exports_restriction(self):
        from controls_probe import fill_contact_test
        with patch('controls_probe.run_window_script',return_value={}) as run:
            fill_contact_test(window(1),'+5516991234567','4',require_empty=True)
            self.assertTrue(run.call_args.args[2]['require_empty'])
        qid=self.configure();self.queue.start(qid);job=self.fill(qid);r=limit(job['window'])
        self.queue.accept(qid,job['id'],[r,r])
        out=Path(self.tmp.name)/'queue.json';self.queue.export(out)
        result=json.loads(out.read_text())
        self.assertEqual(result['jobs'][0]['state'],'paused')
        self.assertEqual(len(result['observations'][0]['reports']),2)
        self.assertEqual(result['jobs'][1]['account'],'B')

    def app(self):
        from app import App
        obj=App.__new__(App)
        obj.store=self.store;obj.adding_queue=self.queue
        obj.contact_results=queue.Queue();obj.probing=False
        obj.root=SimpleNamespace(after=lambda *args:None)
        obj.probe_button=SimpleNamespace(configure=lambda **kwargs:None)
        obj.status=SimpleNamespace(set=lambda value:None)
        obj.refresh=lambda **kwargs:None;obj.refresh_adding_queue=lambda:None
        return obj

    def run_observer(self,obj,reports):
        class ImmediateThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
        with patch('app.threading.Thread',ImmediateThread),patch('app.time.sleep'),patch('app.inspect_controls',side_effect=reports):
            obj.tick_contact_queue();obj.poll_contact_worker()

    def test_gui_restriction_popup_stops_all_accounts(self):
        qid=self.configure();self.queue.start(qid);job=self.fill(qid)
        obj=self.app();r=limit(job['window'])
        with patch('app.messagebox.showwarning') as popup:
            self.run_observer(obj,[r,copy.deepcopy(r)])
            popup.assert_called_once()
            self.assertIn('A',popup.call_args.args[1])
        self.assertIsNone(self.queue.current(qid))
        self.assertEqual(self.queue.snapshot(qid)['jobs'][1]['state'],'queued')
        self.assertEqual(self.store.next_contact_number(),1)

    def test_gui_unknown_dialog_stops_and_saves_evidence_without_touching_B(self):
        qid=self.configure();self.queue.start(qid);job=self.fill(qid);r=limit(job['window'])
        r['controls'][0]['name']='Unrecognized server error'
        obj=self.app();self.run_observer(obj,[r,copy.deepcopy(r)])
        self.assertEqual(self.queue.snapshot(qid)['state'],'review')
        self.assertEqual(self.queue.snapshot(qid)['jobs'][1]['state'],'queued')
        out=Path(self.tmp.name)/'report.json';self.queue.export(out)
        self.assertEqual(json.loads(out.read_text())['last_read']['reports'][0]['controls'][0]['name'],'Unrecognized server error')

    def test_gui_stop_during_read_cannot_advance_or_allocate(self):
        qid=self.configure();self.queue.start(qid);job=self.fill(qid);r=limit(job['window'])
        obj=self.app();obj.probing=True
        obj.contact_results.put(('observe',qid,job,[r,r],None))
        self.queue.stop(qid)
        obj.poll_contact_worker()
        self.assertEqual(self.queue.snapshot(qid)['state'],'stopped')
        self.assertEqual(self.store.next_contact_number(),1)
        self.assertEqual(self.queue.snapshot(qid)['jobs'][1]['state'],'queued')


if __name__=='__main__':unittest.main()

class SingleProfileObservationTests(unittest.TestCase):
    def test_single_read_only_for_verified_submitted_profile(self):
        job={'window':window(1),'item':{'source':'phone','value':'+5516991234567'},'preview_number':94}
        r=profile(job)
        self.assertIsNone(confirmed_observation([r],job['window'],job['item'],94))
        self.assertEqual(confirmed_observation([r],job['window'],job['item'],94,single_profile=True)['outcome'],'added')
        for key,value in [('truncated',True),('errors',['unavailable']),('window_handle',999)]:
            bad=copy.deepcopy(r);bad[key]=value
            self.assertIsNone(confirmed_observation([bad],job['window'],job['item'],94,single_profile=True))
        self.assertIsNone(confirmed_observation([r],job['window'],job['item'],95,single_profile=True))
        self.assertIsNone(confirmed_observation([limit(job['window'])],job['window'],job['item'],94,single_profile=True))
