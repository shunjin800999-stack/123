"""UI start account controls the frozen group-selection window suffix."""
import copy
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from app import App


class PinnedStartAccountTests(unittest.TestCase):
    def scene(self,start='账号15',*,names=None):
        names=names or ['账号11','账号12','账号13','账号14','账号15','账号16','账号17']
        app=App.__new__(App)
        app.windows=[{'hwnd':100+i,'pid':200+i,'path':f'C:\\test\\{i}\\Telegram.exe',
                      'title':'Telegram','candidate':True,'minimized':False} for i in range(len(names))]
        keys=tuple(str(i) for i in range(len(names)))
        app.window_tree=Mock();app.window_tree.selection.return_value=tuple(reversed(keys))
        app.window_tree.get_children.return_value=keys
        app.group_account=SimpleNamespace(get=lambda:start)
        records={name:{'account':name,'window':copy.deepcopy(w)} for name,w in zip(names,app.windows)}
        app.pinned_groups=Mock()
        app.pinned_groups.accounts.side_effect=lambda:list(records)
        app.pinned_groups.get.side_effect=lambda name:copy.deepcopy(records.get(name))
        return app,records

    def selection(self,app,start):
        return app.selected_pinned_observations(require_account_picker=False,start_account=start)

    def test_middle_account_begins_queue_and_excludes_completed_predecessors(self):
        app,records=self.scene();before=copy.deepcopy(records)
        self.assertEqual([r['account'] for r in self.selection(app,'账号15')],['账号15','账号16','账号17'])
        self.assertEqual(records,before)
        app.pinned_groups.save.assert_not_called()
        app.pinned_groups.save_many.assert_not_called()

    def test_first_or_last_account_keeps_exact_remaining_queue(self):
        for start,expected in [('账号11',[f'账号{n}' for n in range(11,18)]),('账号17',['账号17'])]:
            with self.subTest(start=start):
                app,_=self.scene(start)
                self.assertEqual([r['account'] for r in self.selection(app,start)],expected)

    def test_scan_queue_order_is_preserved_instead_of_selection_click_order(self):
        app,_=self.scene('账号15',names=['账号11','账号15','账号18','账号16'])
        self.assertEqual([r['account'] for r in self.selection(app,'账号15')],['账号15','账号18','账号16'])

    def test_custom_account_names_can_be_used_as_start(self):
        app,_=self.scene('备用账号',names=['主账号','备用账号','工作账号'])
        self.assertEqual([r['account'] for r in self.selection(app,'备用账号')],['备用账号','工作账号'])

    def test_unselected_or_rebound_start_never_falls_back_to_first_window(self):
        for kind in ['unselected','rebound']:
            with self.subTest(kind=kind):
                app,records=self.scene()
                if kind=='unselected':app.window_tree.selection.return_value=('0','1','5','6')
                else:records['账号15']['window']['pid']=999
                with self.assertRaisesRegex(ValueError,'绑定窗口不在扫描页选中窗口'):
                    self.selection(app,'账号15')

    def test_empty_or_missing_start_binding_is_rejected(self):
        for start,reason in [('', '选择搜索选人的起始账号'),('未知账号','尚未识别并记录')]:
            with self.subTest(start=start):
                app,_=self.scene(start)
                with self.assertRaisesRegex(ValueError,reason):self.selection(app,start)

    def test_unbound_skipped_account_does_not_block_later_start(self):
        app,records=self.scene();del records['账号12']
        self.assertEqual([r['account'] for r in self.selection(app,'账号15')],['账号15','账号16','账号17'])

    def test_unbound_remaining_account_still_blocks_selection(self):
        app,records=self.scene();del records['账号16']
        with self.assertRaisesRegex(ValueError,'没有唯一的置顶群记录'):self.selection(app,'账号15')

    def test_cleanup_without_start_account_still_uses_all_selected_windows(self):
        app,_=self.scene()
        records=app.selected_pinned_observations(require_account_picker=False)
        self.assertEqual([r['account'] for r in records],[f'账号{n}' for n in range(11,18)])

    def test_later_run_preserves_earlier_completed_plan_and_selection_evidence(self):
        from store import Store
        from groups import GroupCatalog
        from pinned_members import PinnedMemberPlans
        from test_pinned_members import observation,selected
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'progress.sqlite3');self.addCleanup(store.db.close)
            GroupCatalog(store);plans=PinnedMemberPlans(store)
            records={name:observation(i,name) for i,name in enumerate(['账号14','账号15'],1)}
            frozen={name:plans.save(record,numbers) for (name,record),numbers in
                    zip(records.items(),[['715','734'],['735','754']])}
            job={'account':'账号14','slot':1,'plan_id':frozen['账号14']['plan_id'],
                 'state':'selection_finished','numbers':['715','734'],
                 'selection':selected(records['账号14'],1,['715','734'])}
            report={'scope':'pinned_member_selection_queue','final_invite_clicked':False,
                    'contact_database_updated':False,'jobs':[job]}
            plans.save_run(report)
            before_plan=plans.get('账号14');before_state=plans.states('账号14')
            app,_=self.scene('账号15',names=['账号14','账号15'])
            app.windows=[r['window'] for r in records.values()]
            app.pinned_groups.get.side_effect=lambda name:copy.deepcopy(records.get(name))
            entries=plans.entries(self.selection(app,'账号15'),1)
            self.assertEqual([p['record']['account'] for p in entries],['账号15'])
            later=dict(report,jobs=[{'account':'账号15','slot':1,'plan_id':frozen['账号15']['plan_id'],
                'state':'review','numbers':['735','754'],'selection':{'selection_verified':False}}])
            plans.save_run(later)
            self.assertEqual(plans.get('账号14'),before_plan)
            self.assertEqual(plans.states('账号14'),before_state)

    def test_group_one_and_two_controllers_freeze_only_start_and_later_plans(self):
        for slot in (1,2):
            with self.subTest(slot=slot),tempfile.TemporaryDirectory() as tmp:
                app,records=self.scene();before=copy.deepcopy(records)
                app.group_operation_ready=Mock();app.store=Mock()
                app.pinned_member_plans=Mock()
                app.pinned_member_plans.entries.side_effect=lambda records,slot:[
                    {'record':r,'numbers':['735']} for r in records]
                app.member_pause=threading.Event();app.member_selection_active=False
                app.probe_button=Mock();app.status=Mock();app.root=Mock();app.probe_queue=queue.Queue()
                result={'ok':True,'scope':'pinned_member_selection_queue','jobs':[],
                        'final_invite_clicked':False,'contact_database_updated':False}
                def immediate_thread(*,target,daemon):
                    return SimpleNamespace(start=target)
                with patch('app.DATA',Path(tmp)),patch('backup_ocr.require_environment'),\
                     patch('app.saved_contact_snapshot',return_value=[]),\
                     patch('app.threading.Thread',side_effect=immediate_thread),\
                     patch('app.select_pinned_member_queue',return_value=result) as select:
                    app.select_pinned_members(slot)
                used_records,used_slot=app.pinned_member_plans.entries.call_args.args
                self.assertEqual(used_slot,slot)
                self.assertEqual([r['account'] for r in used_records],['账号15','账号16','账号17'])
                plans,group=select.call_args.args[:2]
                self.assertEqual(group,slot)
                self.assertEqual([p['record']['account'] for p in plans],['账号15','账号16','账号17'])
                self.assertEqual(list(select.call_args.kwargs['contacts_by_account']),['账号15','账号16','账号17'])
                self.assertTrue(select.call_args.kwargs['fast_visible'])
                self.assertIn('从 账号15 开始',app.status.set.call_args.args[0])
                self.assertEqual(app.probe_queue.get_nowait(),(True,{'pinned_members_result':result}))
                self.assertEqual(records,before)


if __name__=='__main__':unittest.main()
