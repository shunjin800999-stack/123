import copy
import json
import tempfile
import unittest
from pathlib import Path

from groups import GroupCatalog
from pinned_groups import PinnedGroupBindings, pinned_pair, consistent_pair
from store import Store
from test_contact_queue import window
from test_groups import export_data


def report(w=None, portuguese=False):
    w=w or window(1)
    labels=['Grupo, 你好123, Fixado','Grupo, 456哈哈, Fixado'] if portuguese else [
        'Group, 你好123, Muted, Pinned','Group, 456哈哈, Muted, Pinned, Marcia: preview, Received, at 13:52']
    fields=['Tipo','Nome','Fixado'] if portuguese else ['Type','Name','Muted','Pinned']
    return {'ok':True,'read_only':True,'scope':'group_catalog_probe','truncated':False,
        'errors':[],'final_invite_clicked':False,'window_handle':w['hwnd'],'process_id':w['pid'],
        'list_class':'class Dialogs::InnerWidget','list_runtime_id':f'list:{w["hwnd"]}',
        'candidates':[{'type':'ListItem','runtime_id':f'row:{w["hwnd"]}:{i}','name':label,
            'label_truncated':False,'child_field_names':fields} for i,label in enumerate(labels)] +
            [{'type':'DataItem','name':'Message'}]}


class PinnedGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.store=Store(self.path/'db.sqlite3');self.catalog=GroupCatalog(self.store)
        self.pins=PinnedGroupBindings(self.store)

    def tearDown(self):self.store.close();self.tmp.cleanup()

    def test_finished_contact_only_batch_can_bind_but_running_and_held_cannot(self):
        from contact_queue import ContactQueue
        ContactQueue(self.store)
        r=report();self.pins.save('A',window(1),r,r)
        self.store.db.execute("INSERT INTO batches(id,account,target,next_number) VALUES(1,'A',20,428)")
        self.store.db.execute("INSERT INTO contact_queues(id,state,target_mode) VALUES(1,'done','contact')")
        self.store.db.execute("INSERT INTO contact_queue_jobs(queue_id,position,batch_id,window_json,state) VALUES(1,1,1,?,'no_list')",(json.dumps(window(1)),))
        self.store.db.commit()
        changed=report();changed['candidates'][0]['name']='Group, fresh, Muted, Pinned'
        self.pins.save('A',window(1),changed,changed)
        self.store.db.execute("UPDATE contact_queues SET state='running'");self.store.db.commit()
        with self.assertRaises(ValueError):self.pins.save('A',window(1),r,r)
        self.store.db.execute("UPDATE contact_queues SET state='done'")
        self.store.db.execute("INSERT INTO items(source,seq,value,origin,origin_line,status,batch_id) VALUES('username',109,'@test','test',1,'uncertain',1)")
        self.store.db.commit()
        with self.assertRaises(ValueError):self.pins.save('A',window(1),r,r)

    def test_archived_confirmed_plan_no_longer_blocks_binding(self):
        r=report();self.pins.save('A',window(1),r,r)
        self.store.db.execute("INSERT INTO batches(id,account,target,next_number,status) VALUES(1,'A',20,428,'archived')")
        self.store.db.executescript("CREATE TABLE pinned_member_plans(account TEXT,plan_json TEXT); CREATE TABLE pinned_member_states(account TEXT,job_json TEXT);")
        plan={'source':'confirmed_batch','batch_id':1,'plan_id':'old'}
        self.store.db.execute('INSERT INTO pinned_member_plans VALUES(?,?)',('A',json.dumps(plan)));self.store.db.commit()
        changed=report();changed['candidates'][0]['name']='Group, fresh, Muted, Pinned'
        self.pins.save('A',window(1),changed,changed)
        self.store.db.execute("UPDATE batches SET status='waiting'");self.store.db.commit()
        with self.assertRaises(ValueError):self.pins.save('A',window(1),r,r)

    def test_english_and_portuguese_actual_labels_and_count_excludes_fields(self):
        for pt in (False,True):
            pair=pinned_pair(report(portuguese=pt),window(1),require_fields=True)
            self.assertEqual([g['name'] for g in pair['targets']],['你好123','456哈哈'])
            self.assertEqual(pair['observed_chat_rows'],2)
            self.assertFalse(pair['use_for_invitation'])
            self.assertFalse(pair['stable_group_ids_available'])

    def test_names_are_not_hardcoded_and_same_named_groups_are_distinct_rows(self):
        r=report()
        for row in r['candidates'][:2]:row['name']='Group, 新群🐈, Pinned'
        pair=pinned_pair(r,window(1),require_fields=True)
        self.assertEqual([g['name'] for g in pair['targets']],['新群🐈','新群🐈'])
        self.assertNotEqual(pair['targets'][0]['runtime_id'],pair['targets'][1]['runtime_id'])

    def test_message_preview_pinned_word_does_not_match_metadata(self):
        r=report();r['candidates'][0]['name']='Group, test, Sender: Pinned, Received, at 1:00'
        with self.assertRaises(ValueError):pinned_pair(r)
        r['candidates'][0]['name']='Channel, Group, Pinned'
        with self.assertRaises(ValueError):pinned_pair(r)

    def test_ambiguous_comma_title_and_unsupported_language_stop(self):
        for name in ('Group, foo, bar, Pinned','Groupe, nom, Épinglé'):
            r=report();r['candidates'][0]['name']=name
            with self.assertRaises(ValueError):pinned_pair(r)

    def test_old_reports_preview_only_cannot_be_saved_without_direct_field_evidence(self):
        r=report()
        for row in r['candidates'][:2]:row.pop('child_field_names')
        self.assertEqual(len(pinned_pair(r)['targets']),2)
        with self.assertRaises(ValueError):self.pins.save('A',window(1),r,r)
        self.assertIsNone(self.pins.get('A'))

    def test_failures_truncation_identity_and_duplicate_row_ids_stop(self):
        variants=[]
        for key,value in [('ok',False),('truncated',True),('errors',['error']),
                ('scope','profile'),('window_handle',999),('process_id',999),('read_only',False)]:
            r=report();r[key]=value;variants.append(r)
        r=report();r['candidates'][1]['runtime_id']=r['candidates'][0]['runtime_id'];variants.append(r)
        r=report();r['candidates'][0]['label_truncated']=True;variants.append(r)
        for r in variants:
            with self.assertRaises(ValueError):self.pins.save('A',window(1),r,r)
        self.assertEqual(self.pins.accounts(),[])

    def test_changed_pair_or_order_between_reads_does_not_overwrite_previous_record(self):
        r=report();self.pins.save('A',window(1),r,r)
        changed=copy.deepcopy(r);changed['candidates'][:2]=list(reversed(changed['candidates'][:2]))
        with self.assertRaises(ValueError):self.pins.save('A',window(1),r,changed)
        self.assertEqual(self.pins.get('A')['targets'][0]['name'],'你好123')

    def test_previews_can_change_but_identity_name_pin_and_order_must_match(self):
        a=report();b=copy.deepcopy(a)
        b['candidates'][1]['name']='Group, 456哈哈, Muted, Pinned, someone: new message'
        self.assertEqual(consistent_pair(a,b,window(1))['targets'][1]['name'],'456哈哈')
        b['list_runtime_id']='changed'
        with self.assertRaises(ValueError):consistent_pair(a,b,window(1))

    def test_accounts_same_names_stay_separate_and_observation_persists_without_previews(self):
        for i,a in enumerate(('A','B'),1):
            r=report(window(i),portuguese=i==2);self.pins.save(a,window(i),r,r)
        self.assertEqual(self.pins.accounts(),['A','B'])
        self.assertNotEqual(self.pins.get('A')['window'],self.pins.get('B')['window'])
        self.assertNotIn('preview',json.dumps(self.pins.get('A')))
        self.assertEqual(PinnedGroupBindings(self.store).get('B')['targets'][0]['language'],'pt')
        self.assertEqual(self.store.addition_stats()['today_added'],0)
        self.assertEqual(self.store.next_contact_number(),1)

    def test_one_window_cannot_be_registered_to_two_account_labels(self):
        r=report();self.pins.save('A',window(1),r,r)
        with self.assertRaises(ValueError):self.pins.save('B',window(1),r,r)
        self.assertIsNone(self.pins.get('B'))

    def test_pin_record_does_not_supply_executable_queue_targets(self):
        r=report();self.pins.save('A',window(1),r,r)
        with self.assertRaises(ValueError):self.catalog.queue_targets('A',window(1))

    def test_pin_and_export_bindings_cannot_share_window_between_accounts(self):
        path=self.path/'result.json';path.write_text(json.dumps(export_data()),encoding='utf-8')
        self.catalog.import_file('B',path)
        r=report();self.pins.save('A',window(1),r,r)
        with self.assertRaises(ValueError):self.catalog.bind_window('B',window(1))
        self.catalog.bind_window('B',window(2))
        r=report(window(2))
        with self.assertRaises(ValueError):self.pins.save('C',window(2),r,r)


if __name__=='__main__':unittest.main()
