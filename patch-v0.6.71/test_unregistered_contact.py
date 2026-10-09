import copy,json,unittest
from contact_queue import unregistered_evidence,ContactQueue
from store import Store
from test_contact_submit import ContactSubmitTests,submitted
from test_contact_queue import limit

def result(job):
 r=limit(job['window']);r.update(scope='contact_dialog')
 r['controls']=[{'type':'Group','class_name':'class Ui::BoxLayerWidget','visible':True},
  {'type':'Window','class_name':'class AddContactBox','visible':True},
  {'type':'Text','name':'New Contact','visible':True},
  {'type':'Button','name':'Try someone else','class_name':'class Ui::RoundButton','visible':True,'enabled':True}]
 return r

def dismissed(job,dialog='modal-1'):
 return {'ok':True,'scope':'unregistered_contact','window_handle':job['window']['hwnd'],
  'process_id':job['window']['pid'],'main_runtime_id':job['fill_report']['main_runtime_id'],
  'dialog_runtime_id':dialog,'number':str(job['preview_number']),
  **{k:True for k in ('process_path_verified','signature_verified','dismiss_attempted','dismiss_invoked','result_absent')},
  **{k:False for k in ('create_attempted','final_invite_clicked','contact_database_updated')}}

class UnregisteredTests(unittest.TestCase):
 setUp=ContactSubmitTests.setUp
 tearDown=ContactSubmitTests.tearDown
 fill=ContactSubmitTests.fill
 app=ContactSubmitTests.app
 def submission(self,recovery=False):
  job=self.fill();job=self.queue.claim_submit(self.qid,job['id']);r=submitted(job,dialog=True)
  if recovery:
   r.update(ok=False,stage='wait_result',state='review');r.pop('result_runtime_id')
   self.queue.save_submit_result(self.qid,job['id'],job['item_id'],r,'timeout')
   self.queue.stop(self.qid,'timeout',review=True)
  else:
   self.queue.save_submit_result(self.qid,job['id'],job['item_id'],r)
   self.queue.finish_submit(self.qid,job['id'],job['item_id'])
  return job
 def test_automatic_skip_keeps_number_and_account_without_switching_username(self):
  job=self.submission();r=result(job)
  claimed,p=self.queue.claim_unregistered(self.qid,job['id'],[r,copy.deepcopy(r)])
  self.queue.save_unregistered_result(self.qid,job['id'],job['item_id'],dismissed(job))
  self.queue.finish_unregistered(self.qid,job['id'],job['item_id'])
  nxt=self.queue.current(self.qid)
  self.assertEqual(nxt['account'],'A');self.assertNotEqual(nxt['item_id'],job['item_id'])
  self.assertEqual(self.store.next_contact_number(),12)
  item=next(x for x in self.store.rows() if x['id']==job['item_id'])
  self.assertEqual(item['status'],'lookup_failed');self.assertIsNone(item['contact_number'])
  self.assertEqual(self.store.batch(job['batch_id'])['mode'],'phone')
  self.assertEqual(self.queue.claim_fill(self.qid,nxt['id'])['preview_number'],12)
 def test_recovery_accepts_explicit_same_phone_reproduction_not_create_retry(self):
  job=self.submission(True);r=result(job);r['scope_runtime_id']='manual-reproduction'
  claimed,p=self.queue.claim_unregistered(self.qid,job['id'],[r,copy.deepcopy(r)],recovery=True)
  self.assertEqual(p['dialog_runtime_id'],'manual-reproduction')
  self.queue.save_unregistered_result(self.qid,job['id'],job['item_id'],dismissed(job,'manual-reproduction'))
  self.queue.finish_unregistered(self.qid,job['id'],job['item_id'])
  self.assertEqual(self.store.next_contact_number(),12)
  self.assertEqual(self.queue.current(self.qid)['account'],'A')
 def test_two_failures_switch_account_and_survive_restart(self):
  ids=[]
  for index in range(2):
   job=self.submission();r=result(job);ids.append(job['item_id'])
   self.queue.claim_unregistered(self.qid,job['id'],[r,r])
   self.queue.save_unregistered_result(self.qid,job['id'],job['item_id'],dismissed(job))
   self.queue.finish_unregistered(self.qid,job['id'],job['item_id'])
   self.assertEqual(self.store.next_contact_number(),12)
   self.assertEqual(self.queue.current(self.qid)['account'],'A' if index==0 else 'B')
  snap=self.queue.snapshot(self.qid)
  self.assertEqual(snap['state'],'running')
  self.assertEqual(snap['jobs'][0]['state'],'paused')
  self.assertEqual(snap['jobs'][0]['lookup_failures'],2)
  self.assertIn('继续下一个账号',snap['jobs'][0]['reason'])
  self.assertEqual(snap['jobs'][1]['state'],'waiting_form')
  self.assertEqual(snap['jobs'][1]['lookup_failures'],0)
  self.assertTrue(all(x['status']=='lookup_failed' for x in self.store.rows() if x['id'] in ids))
  self.assertEqual(self.queue.claim_fill(self.qid,snap['jobs'][1]['id'])['preview_number'],12)
  self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store)
  self.assertIsNone(self.queue.current(self.qid));self.assertEqual(self.store.next_contact_number(),12)
  self.assertEqual(self.queue.snapshot(self.qid)['jobs'][0]['state'],'paused')

 def test_success_resets_consecutive_failures(self):
  from test_contact_queue import profile
  with self.store.db:self.store.db.execute('UPDATE batches SET target=10')
  self.store.import_text('phone','+5516991234504\n+5516991234505\n+5516991234506')
  def fail():
   job=self.submission();r=result(job)
   self.queue.claim_unregistered(self.qid,job['id'],[r,r])
   self.queue.save_unregistered_result(self.qid,job['id'],job['item_id'],dismissed(job))
   self.queue.finish_unregistered(self.qid,job['id'],job['item_id'])
  fail()
  job=self.fill();job=self.queue.claim_submit(self.qid,job['id'])
  self.queue.save_submit_result(self.qid,job['id'],job['item_id'],submitted(job))
  self.queue.finish_submit(self.qid,job['id'],job['item_id'])
  r=profile(job);self.queue.accept(self.qid,job['id'],[r,r])
  fail()
  self.assertEqual(self.queue.snapshot(self.qid)['state'],'running')
  self.assertEqual(self.queue.snapshot(self.qid)['jobs'][0]['lookup_failures'],1)
  self.assertEqual(self.store.next_contact_number(),13)

 def fail_once(self):
  job=self.submission();r=result(job)
  self.queue.claim_unregistered(self.qid,job['id'],[r,r])
  self.queue.save_unregistered_result(self.qid,job['id'],job['item_id'],dismissed(job))
  self.queue.finish_unregistered(self.qid,job['id'],job['item_id'])
  return job

 def test_no_remaining_account_finishes_without_number_increment(self):
  self.store.import_text('phone','+5516991234504')
  for account in ('A','A','B','B'):
   self.assertEqual(self.queue.current(self.qid)['account'],account)
   self.fail_once()
  snap=self.queue.snapshot(self.qid)
  self.assertEqual(snap['state'],'done')
  self.assertIsNone(self.queue.current(self.qid))
  self.assertTrue(all(j['state']=='paused' and j['lookup_failures']==2 for j in snap['jobs']))
  self.assertEqual(self.store.next_contact_number(),12)

 def test_duplicate_completion_does_not_count_or_skip_next_account(self):
  job=self.fail_once()
  with self.assertRaises(ValueError):
   self.queue.finish_unregistered(self.qid,job['id'],job['item_id'])
  self.assertEqual(self.queue.current(self.qid)['account'],'A')
  self.assertEqual(self.queue.snapshot(self.qid)['jobs'][0]['lookup_failures'],1)

 def test_ui_schedules_next_account_without_pause_warning(self):
  from unittest.mock import patch
  self.fail_once()
  job=self.submission();r=result(job)
  claimed,_=self.queue.claim_unregistered(self.qid,job['id'],[r,r])
  a=self.app();scheduled=[];messages=[]
  a.root.after=lambda delay,callback:scheduled.append((delay,callback))
  a.status.set=messages.append
  a.contact_results.put(('dismiss_unregistered',self.qid,claimed,dismissed(job),None))
  with patch('app.messagebox.showwarning') as warning:
   a.poll_contact_worker()
   warning.assert_not_called()
  self.assertEqual(self.queue.current(self.qid)['account'],'B')
  self.assertTrue(any(delay==0 and callback==a.drive_contact_queue for delay,callback in scheduled))
  self.assertTrue(any('下一账号：B' in message for message in messages))

 def test_offline_simulator_matches_two_failure_switch(self):
  from offline_pause_test import Simulation
  simulation=Simulation()
  try:
   a=simulation.fixture.app()
   self.assertEqual(simulation.step(a)['jobs'][0]['lookup_failures'],1)
   snap=simulation.step(a)
   self.assertEqual(snap['jobs'][0]['state'],'paused')
   self.assertEqual(simulation.fixture.queue.current(simulation.fixture.qid)['account'],'账号3')
   with self.assertRaises(ValueError):simulation.step(a)
  finally:simulation.close()

 def test_signature_requires_two_bound_complete_reports_and_no_edits(self):
  job=self.submission();r=result(job)
  for kind in ('button','title','edit','class','pid','truncated','identity','duplicate'):
   a=copy.deepcopy(r);b=copy.deepcopy(r)
   if kind=='button':a['controls'][-1]['name']='OK'
   if kind=='title':a['controls'][2]['name']='Error'
   if kind=='edit':a['controls'].append({'type':'Edit','visible':True})
   if kind=='class':a['controls'][1]['class_name']='class Other'
   if kind=='pid':a['process_id']+=1
   if kind=='truncated':a['truncated']=True
   if kind=='identity':b['scope_runtime_id']='other'
   if kind=='duplicate':a['controls'].append(copy.deepcopy(a['controls'][-1]))
   with self.subTest(kind=kind):self.assertIsNone(unregistered_evidence([a,b],job['window']))
 def test_changed_result_identity_blocked_for_normal_run(self):
  job=self.submission();r=result(job);r['scope_runtime_id']='different'
  with self.assertRaises(ValueError):self.queue.claim_unregistered(self.qid,job['id'],[r,r])
 def test_stop_or_failed_dismissal_never_releases_reserved_phone(self):
  job=self.submission();r=result(job)
  self.queue.claim_unregistered(self.qid,job['id'],[r,r])
  bad=dismissed(job);bad['result_absent']=False
  self.queue.save_unregistered_result(self.qid,job['id'],job['item_id'],bad)
  with self.assertRaises(ValueError):self.queue.finish_unregistered(self.qid,job['id'],job['item_id'])
  self.assertEqual(next(x for x in self.store.rows() if x['id']==job['item_id'])['status'],'reserved')
  self.assertEqual(self.store.next_contact_number(),12)
  self.queue.stop(self.qid)
  with self.assertRaises(ValueError):self.queue.finish_unregistered(self.qid,job['id'],job['item_id'])
 def test_submitted_profile_recovery_advances_once_and_moves_to_b(self):
  from test_contact_queue import profile
  job=self.submission(True);r=profile(job)
  self.queue.recover_submitted_profile(self.qid,job['id'],[r,copy.deepcopy(r)])
  self.assertEqual(self.store.next_contact_number(),13)
  self.assertEqual(self.queue.current(self.qid)['account'],'B')
  a=self.queue.snapshot(self.qid)['jobs'][0]
  self.assertEqual(a['added_numbers'],[12]);self.assertEqual(a['state'],'target_reached')
  with self.assertRaises(ValueError):self.queue.recover_submitted_profile(self.qid,job['id'],[r,r])
  self.assertEqual(self.store.next_contact_number(),13)
 def test_recovery_rejects_wrong_phone_and_changed_profile(self):
  from test_contact_queue import profile
  job=self.submission(True);r=profile(job)
  for kind in ('phone','number','identity','incomplete'):
   a=copy.deepcopy(r);b=copy.deepcopy(r)
   if kind=='phone':a['controls'][0]['name']='+5516999999999'
   if kind=='number':a['controls'][1]['name']='13'
   if kind=='identity':b['scope_runtime_id']='other-profile'
   if kind=='incomplete':b['truncated']=True
   with self.subTest(kind=kind):
    with self.assertRaises(ValueError):self.queue.recover_submitted_profile(self.qid,job['id'],[a,b])
   self.assertEqual(self.store.next_contact_number(),12)
   self.assertEqual(next(x for x in self.store.rows() if x['id']==job['item_id'])['status'],'reserved')
 def test_reopen_during_dismissal_does_not_resubmit_or_release(self):
  job=self.submission();r=result(job);self.queue.claim_unregistered(self.qid,job['id'],[r,r])
  self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store)
  # Queue constructor moves interrupted active queues to review, never retries input.
  self.assertIsNone(self.queue.current(self.qid))
  self.assertEqual(next(x for x in self.store.rows() if x['id']==job['item_id'])['status'],'reserved')

# Prevent unittest from collecting the imported fixture class a second time.
 def test_portuguese_unregistered_recovery_skips_without_consuming_number(self):
  job=self.submission(recovery=True);r=result(job)
  r['controls'][2]['name']='Novo Contato';r['controls'][3]['name']='Tentar outro'
  self.assertIsNotNone(unregistered_evidence([r,copy.deepcopy(r)],job['window']))
  before=self.store.next_contact_number()
  claimed,payload=self.queue.claim_unregistered(self.qid,job['id'],[r,copy.deepcopy(r)],recovery=True)
  self.queue.save_unregistered_result(self.qid,job['id'],job['item_id'],dismissed(job))
  self.queue.finish_unregistered(self.qid,job['id'],job['item_id'])
  self.assertEqual(self.store.next_contact_number(),before)
  self.assertEqual(next(x for x in self.store.rows() if x['id']==job['item_id'])['status'],'lookup_failed')
  bad=copy.deepcopy(r);bad['controls'][2]['name']='New Contact'
  self.assertIsNone(unregistered_evidence([bad,bad],job['window']))

del ContactSubmitTests
