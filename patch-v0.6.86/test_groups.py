import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from groups import GroupCatalog, parse_group_export, read_group_export
from store import Store
from contact_queue import ContactQueue
from test_contact_queue import window


def export_data(owner=100, groups=None):
    if groups is None:groups=[{'id':10,'type':'private_group','name':'同名群'},
        {'id':10,'type':'public_supergroup','name':'同名群'},
        {'id':30,'type':'private_supergroup','name':'第三个群'}]
    return {'personal_information':{'user_id':owner,'first_name':'Test','phone_number':'+5516991234567'},
        'chats':{'list':[dict(g,messages=[{'text':'SECRET_MESSAGE'}]) for g in groups]+
            [{'id':40,'type':'public_channel','name':'频道群名称'},
             {'id':50,'type':'personal_chat','name':'名字叫Group的个人'},
             {'id':60,'type':'bot_chat','name':'群助手'},
             {'id':70,'type':'unknown','name':'未知'}]},
        'left_chats':{'list':[{'id':80,'type':'private_supergroup','name':'已退出的群'}]}}


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.store=Store(self.path/'db.sqlite3');self.groups=GroupCatalog(self.store)

    def tearDown(self):self.store.close();self.tmp.cleanup()

    def file(self,data,name='result.json'):
        path=self.path/name;path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');return path

    def import_A(self,data=None):
        return self.groups.import_file('A',self.file(data or export_data()))

    def targets(self,account='A'):
        catalog=self.groups.catalog(account)
        self.groups.save_targets(account,[g['peer_key'] for g in catalog['groups'][:2]])

    def test_actual_types_not_names_distinguish_groups_channels_bots_and_left_groups(self):
        result=parse_group_export(export_data())
        self.assertEqual(result['group_count'],3)
        self.assertEqual([g['peer_key'] for g in result['groups']],['group:10','supergroup:10','supergroup:30'])
        self.assertEqual(result['counts']['channels'],1)
        self.assertEqual(result['counts']['unknown_types'],1)
        self.assertNotIn('已退出的群',json.dumps(result,ensure_ascii=False))
        self.assertNotIn('SECRET_MESSAGE',json.dumps(result))
        self.assertFalse(result['all_groups_verified'])

    def test_same_names_and_numeric_id_in_different_peer_namespaces_are_distinct_targets(self):
        self.import_A();self.targets()
        catalog=self.groups.catalog('A')
        self.assertEqual([g['name'] for g in catalog['targets']],['同名群','同名群'])
        self.assertEqual(catalog['target_keys'],['group:10','supergroup:10'])
        self.assertTrue(catalog['targets_ready'])

    def test_wrong_account_export_cannot_overwrite_or_duplicate_account_identity(self):
        self.import_A();self.targets()
        with self.assertRaises(ValueError):self.groups.import_file('A',self.file(export_data(200)))
        with self.assertRaises(ValueError):self.groups.import_file('B',self.file(export_data(100)))
        self.assertEqual(self.groups.catalog('A')['owner_id'],'100')
        self.assertTrue(self.groups.catalog('A')['targets_ready'])

    def test_two_accounts_keep_independent_targets_even_when_same_group_ids(self):
        self.import_A();self.targets()
        self.groups.import_file('B',self.file(export_data(200)))
        self.groups.save_targets('B',['supergroup:10','supergroup:30'])
        self.groups.bind_window('A',window(1));self.groups.bind_window('B',window(2))
        self.assertEqual([g['peer_key'] for g in self.groups.queue_targets('A',window(1))],['group:10','supergroup:10'])
        self.assertEqual([g['peer_key'] for g in self.groups.queue_targets('B',window(2))],['supergroup:10','supergroup:30'])

    def test_reimport_rename_preserves_selection_but_missing_target_blocks_queue(self):
        self.import_A();self.targets();self.groups.bind_window('A',window(1))
        data=export_data();data['chats']['list'][0]['name']='新群名称'
        self.import_A(data)
        self.assertEqual(self.groups.catalog('A')['targets'][0]['name'],'新群名称')
        data['chats']['list'].pop(0);self.import_A(data)
        self.assertFalse(self.groups.catalog('A')['targets_ready'])
        with self.assertRaises(ValueError):self.groups.queue_targets('A',window(1))

    def test_exactly_two_current_named_groups_required_and_others_do_not_become_targets(self):
        self.import_A();self.targets()
        for keys in ([],['group:10'],['group:10','group:10'],['group:10','supergroup:10','supergroup:30'],['group:10','supergroup:999']):
            with self.assertRaises(ValueError):self.groups.save_targets('A',keys)
        self.assertEqual(self.groups.catalog('A')['target_keys'],['group:10','supergroup:10'])
        data=export_data();data['chats']['list'][0]['name']=None;self.import_A(data)
        self.assertFalse(self.groups.catalog('A')['targets_ready'])
        with self.assertRaises(ValueError):self.groups.save_targets('A',['group:10','supergroup:30'])

    def test_window_binding_cannot_be_shared_or_guessed_by_title(self):
        self.import_A();self.targets()
        with self.assertRaises(ValueError):self.groups.queue_targets('A',window(1))
        self.groups.bind_window('A',window(1))
        changed=window(1);changed['pid']+=1
        with self.assertRaises(ValueError):self.groups.queue_targets('A',changed)
        self.groups.import_file('B',self.file(export_data(200)))
        with self.assertRaises(ValueError):self.groups.bind_window('B',window(1))

    def test_import_rejects_partial_exports_bad_ids_and_conflicting_duplicate_metadata(self):
        for mutation in ('single_chat','no_owner','float','bool','negative','duplicate'):
            data=export_data()
            if mutation=='single_chat':data={'id':1,'type':'private_group','messages':[]}
            if mutation=='no_owner':data.pop('personal_information')
            if mutation=='float':data['chats']['list'][0]['id']=1.0
            if mutation=='bool':data['personal_information']['user_id']=True
            if mutation=='negative':data['chats']['list'][0]['id']=-1
            if mutation=='duplicate':data['chats']['list'].append({'id':10,'type':'private_group','name':'冲突群'})
            with self.assertRaises(ValueError):parse_group_export(data)
        path=self.path/'incomplete.json';path.write_text('{')
        with self.assertRaises(ValueError):read_group_export(path)
        self.assertEqual(len(self.groups.accounts()),0)

    def test_queue_requires_two_groups_and_freezes_account_group_targets(self):
        self.import_A();self.groups.bind_window('A',window(1))
        self.groups.import_file('B',self.file(export_data(200)));self.targets('B');self.groups.bind_window('B',window(2))
        queue=ContactQueue(self.store,group_catalog=self.groups)
        entries=[{'account':a,'target':1,'window':window(i)} for i,a in enumerate(('A','B'),1)]
        with self.assertRaises(ValueError):queue.configure(entries)
        self.assertEqual(self.store.batches(),[])
        self.targets();qid=queue.configure(entries)
        self.assertEqual(len(queue.snapshot(qid)['jobs'][0]['target_groups']),2)
        self.groups.save_targets('A',['supergroup:10','supergroup:30'])
        with self.assertRaises(ValueError):queue.start(qid)
        self.assertEqual(queue.snapshot(qid)['state'],'configured')

    def test_one_batch_two_groups_has_same_members_and_no_extra_add_count(self):
        self.import_A();self.targets()
        self.store.import_text('phone','+5516991234501\n+5516991234502')
        batch=self.store.create_batch('A',2)
        with self.assertRaises(ValueError):self.groups.plan_for_batch(batch)
        for _ in range(2):
            item,_=self.store.reserve_next(batch);self.store.record_add_result(item['id'],'added')
        stats=self.store.addition_stats();plan=self.groups.plan_for_batch(batch)
        self.assertEqual(plan['groups'][0]['members'],plan['groups'][1]['members'])
        self.assertEqual([m['number'] for m in plan['groups'][0]['members']],['1','2'])
        self.assertEqual(self.store.addition_stats(),stats)
        self.assertFalse(plan['final_invite_clicked'])
        self.groups.save_targets('A',['supergroup:10','supergroup:30'])
        self.assertEqual(self.groups.plan_for_batch(batch),plan)
        self.store.close();self.store=Store(self.path/'db.sqlite3');self.groups=GroupCatalog(self.store)
        self.assertEqual(self.groups.plan_for_batch(batch),plan)

    def test_local_config_export_contains_no_messages_or_owner_phone(self):
        self.import_A();self.targets()
        path=self.path/'config.json';self.groups.export('A',path);text=path.read_text()
        self.assertNotIn('SECRET_MESSAGE',text);self.assertNotIn('+5516991234567',text)
        self.assertEqual(json.loads(text)['scope'],'account_group_configuration')

    def test_batch_plan_uses_queue_snapshot_when_next_batch_config_changes(self):
        self.import_A();self.targets();self.groups.bind_window('A',window(1))
        self.groups.import_file('B',self.file(export_data(200)));self.targets('B');self.groups.bind_window('B',window(2))
        queue=ContactQueue(self.store,group_catalog=self.groups)
        qid=queue.configure([{'account':a,'target':1,'window':window(i)} for i,a in enumerate(('A','B'),1)])
        self.store.import_text('phone','+5516991234501\n+5516991234502')
        job=queue.start(qid)
        queue.stop(qid,'手动登记前停止队列',review=True)
        self.store.record_add_result(job['item_id'],'added')
        self.groups.save_targets('A',['supergroup:10','supergroup:30'])
        plan=self.groups.plan_for_batch(job['batch_id'])
        self.assertEqual([g['peer_key'] for g in plan['groups']],['group:10','supergroup:10'])
        self.assertIsNot(plan['groups'][0]['members'],plan['groups'][1]['members'])

    def test_ui_probe_transport_error_is_exportable_and_does_not_claim_success(self):
        from controls_probe import inspect_groups
        path=self.path/'probe-error.json'
        with patch('controls_probe.run_window_script',side_effect=RuntimeError('window closed')):
            inspect_groups(window(1),path)
        result=json.loads(path.read_text())
        self.assertFalse(result['ok']);self.assertIsNone(result['group_count'])
        self.assertFalse(result['all_groups_scanned'])

    def test_old_single_group_confirmation_cannot_complete_two_group_plan(self):
        self.import_A();self.targets();self.store.import_text('phone','+5516991234501')
        batch=self.store.create_batch('A',1)
        item,_=self.store.reserve_next(batch);self.store.record_add_result(item['id'],'added')
        self.groups.plan_for_batch(batch);self.store.mark_waiting(batch)
        with self.assertRaisesRegex(ValueError,'两个目标群'):self.store.confirm_invited([item['id']])
        self.assertEqual(self.store.rows()[0]['status'],'pending_invite')
        self.assertEqual(self.store.addition_stats()['total_added'],1)

    def test_ui_probe_failure_still_saves_diagnostic_without_claiming_group_count(self):
        from controls_probe import inspect_groups, WindowActionError
        report={'ok':False,'read_only':True,'scope':'group_catalog_probe','group_count':None,'all_groups_scanned':False,'error':'unsupported list'}
        path=self.path/'probe.json'
        with patch('controls_probe.run_window_script',side_effect=WindowActionError('unsupported list',report)):
            inspect_groups(window(1),path)
        saved=json.loads(path.read_text());self.assertIsNone(saved['group_count'])
        self.assertFalse(saved['all_groups_scanned']);self.assertFalse(saved['ok'])


if __name__=='__main__':unittest.main()
