"""Durable opening-to-fill handoff and exact A/B/global-number integration."""
import copy
import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from store import Store
from contact_queue import ContactQueue
from test_contact_queue import window, profile
from test_contact_open import opened
from controls_probe import WindowActionError
from pinned_groups import PinnedGroupBindings
from groups import GroupCatalog
from test_pinned_groups import report


class ImmediateThread:
    def __init__(self,target,**kwargs):self.target=target
    def start(self):self.target()


class QueueOpeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite3'
        self.store=Store(self.path);GroupCatalog(self.store);pins=PinnedGroupBindings(self.store)
        for i,a in enumerate(('A','B'),1):
            w=window(i);r=report(w);pins.save(a,w,r,copy.deepcopy(r))
        self.queue=ContactQueue(self.store,pinned_groups=pins)
        self.store.import_text('phone','+5516991234501\n+5516991234502\n+5516991234503')
        self.store.set_next_number(10)
        self.qid=self.queue.configure([{'account':a,'window':window(i),'target':1} for i,a in enumerate(('A','B'),1)])
        self.queue.start(self.qid)
        from app import App
        self.app=App.__new__(App);self.app.store=self.store;self.app.adding_queue=self.queue
        self.app.contact_results=queue.Queue();self.app.probing=False
        self.app.root=SimpleNamespace(after=lambda *args:None)
        self.app.probe_button=SimpleNamespace(configure=lambda **kwargs:None)
        self.messages=[];self.app.status=SimpleNamespace(set=self.messages.append)
        self.app.refresh=lambda **kwargs:None;self.app.refresh_adding_queue=lambda:None

    def tearDown(self):self.store.close();self.tmp.cleanup()

    def ordinary(self,job):
        return {'ok':True,'read_only':True,'scope':'window','scope_class':'class MainWindow',
            'window_handle':job['window']['hwnd'],'process_id':job['window']['pid'],
            'scope_runtime_id':'root','truncated':True,'errors':[],'controls':[]}

    def observe(self,reports):
        with patch('app.threading.Thread',ImmediateThread),patch('app.time.sleep'),patch('app.inspect_controls',side_effect=reports):
            self.app.tick_contact_queue();self.app.poll_contact_worker()

    def open_and_fill(self):
        job=self.queue.current(self.qid)
        def run(w,script,payload):
            current=self.queue.current(self.qid)
            self.assertEqual(current['state'],'opening')
            self.assertEqual(current['item_id'],job['item_id'])
            self.assertEqual(self.store.next_contact_number(),10 if job['account']=='A' else 11)
            self.assertEqual(script,'open_contact.ps1');self.assertEqual(payload,{'executable_path':w['path']})
            self.assertEqual(w,job['window'])
            self.assertIsNone(current['preview_number'])
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM contact_queue_openings WHERE job_id=?',(job['id'],)).fetchone()[0],1)
            return opened(w)
        def fill(w,phone,number,**kwargs):
            self.assertEqual(self.queue.current(self.qid)['state'],'filling')
            self.assertEqual((w,phone),(job['window'],job['item']['value']))
            self.assertEqual(kwargs,{'require_empty':True})
            return {'ok':True,'mode':'fill_only','number':number,'phone_matches':True,'empty_guard':True,
                'contact_created':False,'final_invite_clicked':False}
        with patch('app.threading.Thread',ImmediateThread),patch('app.run_window_script',side_effect=run) as opening,patch('app.fill_contact_test',side_effect=fill) as filling:
            self.observe([self.ordinary(job)])
            self.app.poll_contact_worker();self.app.poll_contact_worker()
            opening.assert_called_once();filling.assert_called_once()
        return self.queue.current(self.qid)

    def test_A10_confirm_then_B11_without_manual_form_open_and_complete_export(self):
        a=self.open_and_fill();self.assertEqual((a['account'],a['state'],a['preview_number']),('A','filled',10))
        self.assertEqual(self.store.next_contact_number(),10)
        r=profile(a);self.observe([r,copy.deepcopy(r)])
        self.assertEqual(self.queue.current(self.qid)['account'],'B')
        b=self.open_and_fill();self.assertEqual(b['preview_number'],11)
        r=profile(b);self.observe([r,copy.deepcopy(r)])
        snap=self.queue.snapshot(self.qid)
        self.assertEqual(snap['state'],'done');self.assertEqual(snap['next_number'],12)
        self.assertEqual([j['added_numbers'] for j in snap['jobs']],[[10],[11]])
        path=Path(self.tmp.name)/'export.json';self.queue.export(path);saved=json.loads(path.read_text())
        self.assertEqual(len(saved['form_openings']),2)
        self.assertTrue(all(a['report']['state']=='opened_empty' and a['error'] is None for a in saved['form_openings']))
        self.assertFalse(saved['contact_submit_automated']);self.assertFalse(saved['final_invite_clicked'])

    def test_uncertain_launch_kept_in_report_no_fill_retry_number_or_B(self):
        job=self.queue.current(self.qid);failure=opened(job['window']);failure.update(ok=False,stage='wait_for_form')
        with patch('app.threading.Thread',ImmediateThread),patch('app.run_window_script',side_effect=WindowActionError('did not appear',failure)) as opening,patch('app.fill_contact_test') as fill:
            self.observe([self.ordinary(job)]);self.app.poll_contact_worker();self.app.tick_contact_queue()
            opening.assert_called_once();fill.assert_not_called()
        snap=self.queue.snapshot(self.qid)
        self.assertEqual(snap['state'],'review');self.assertEqual(snap['jobs'][1]['state'],'queued')
        self.assertEqual(self.store.next_contact_number(),10)
        self.assertEqual(self.store.rows()[0]['status'],'reserved')
        path=Path(self.tmp.name)/'export.json';self.queue.export(path)
        action=json.loads(path.read_text())['form_openings'][0]
        self.assertEqual(action['report'],failure);self.assertIn('did not appear',action['error'])

    def test_wrong_window_positive_open_never_fills(self):
        job=self.queue.current(self.qid)
        with patch('app.threading.Thread',ImmediateThread),patch('app.run_window_script',return_value=opened(window(2))),patch('app.fill_contact_test') as fill:
            self.observe([self.ordinary(job)]);self.app.poll_contact_worker();fill.assert_not_called()
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'review')
        self.assertEqual(self.store.next_contact_number(),10)

    def test_stop_during_open_keeps_late_report_without_fill_or_advance(self):
        job=self.queue.claim_open(self.qid,self.queue.current(self.qid)['id'])
        self.queue.stop(self.qid)
        self.app.contact_results.put(('open',self.qid,job,opened(job['window']),None))
        with patch('app.fill_contact_test') as fill:self.app.poll_contact_worker();fill.assert_not_called()
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'stopped')
        self.assertEqual(self.store.next_contact_number(),10)
        self.assertIsNotNone(self.store.db.execute('SELECT report_json FROM contact_queue_openings').fetchone()[0])

    def test_restart_after_open_claim_never_resumes_or_reopens(self):
        job=self.queue.claim_open(self.qid,self.queue.current(self.qid)['id'])
        self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store,pinned_groups=PinnedGroupBindings(self.store))
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'review')
        self.assertIsNone(self.queue.current(self.qid))
        with self.assertRaises(ValueError):self.queue.claim_open(self.qid,job['id'])
        self.assertEqual(self.store.next_contact_number(),10)

    def test_closed_verified_form_cannot_trigger_second_open_or_restore_as_unattempted(self):
        job=self.queue.claim_open(self.qid,self.queue.current(self.qid)['id'])
        self.queue.save_open_result(self.qid,job['id'],job['item_id'],opened(job['window']))
        self.queue.finish_open(self.qid,job['id'],job['item_id'])
        with self.assertRaisesRegex(ValueError,'已尝试'):self.queue.claim_open(self.qid,job['id'])
        self.queue.stop(self.qid);self.queue.clear()
        with self.assertRaisesRegex(ValueError,'尝试打开'):self.queue.restore_waiting(self.qid)
        self.assertEqual(self.store.next_contact_number(),10)

    def test_profile_while_preparing_next_person_waits_without_open_or_fill(self):
        job=self.queue.current(self.qid);r=profile({**job,'preview_number':9})
        with patch('app.run_window_script') as run,patch('app.fill_contact_test') as fill:
            self.observe([r]);run.assert_not_called();fill.assert_not_called()
        self.assertEqual(self.queue.current(self.qid)['state'],'waiting_form')
        self.assertIn('关闭资料面板',self.messages[-1])

    def test_duplicate_executable_paths_are_rejected_before_claim(self):
        job=self.queue.current(self.qid)
        second=window(2);second['path']=window(1)['path']
        with self.store.db:self.store.db.execute('UPDATE contact_queue_jobs SET window_json=? WHERE queue_id=? AND position=2',(json.dumps(second),self.qid))
        with self.assertRaisesRegex(ValueError,'同一程序路径'):self.queue.claim_open(self.qid,job['id'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM contact_queue_openings').fetchone()[0],0)
        self.assertEqual(self.queue.current(self.qid)['state'],'waiting_form')


if __name__=='__main__':unittest.main()
