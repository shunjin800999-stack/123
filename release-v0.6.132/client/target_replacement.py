"""Explicit replacement of an unfinished batch's two invitation targets.

No Telegram input. Preserve contact and addition-queue history. New plan IDs
make every previous selection/confirmation ineligible for the new groups.
"""
import copy
import json
import uuid

from group_navigation import navigation_entries
from pinned_members import same_binding
from pinned_scan import path_key


class TargetReplacement:
    def __init__(self,store,bindings,plans):
        self.store=store;self.db=store.db;self.bindings=bindings;self.plans=plans
        self.db.execute('''CREATE TABLE IF NOT EXISTS pinned_target_replacements (
            id INTEGER PRIMARY KEY, report_json TEXT NOT NULL,
            saved TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')))''')
        self.db.commit()

    def preview(self,records):
        records=navigation_entries(copy.deepcopy(records))
        if len({path_key(r['window']) for r in records})!=len(records):raise ValueError('不同账号不能使用同一程序路径')
        if self.db.execute("SELECT 1 FROM contact_queues WHERE state IN ('running','configured')").fetchone():
            raise ValueError('添加队列正在执行或等待开始，先结束队列再更换目标群')
        rows=[]
        for record in records:
            account=record['account'];old=self.bindings.get(account);plan=self.plans.get(account)
            if (not old or not plan or plan.get('source')!='confirmed_batch'
                    or not same_binding(old,plan['record'])):
                raise ValueError(f'{account} 缺少对应的已冻结数据库批次名单，不能更换本批目标')
            if any(old['window'].get(k)!=record['window'].get(k) for k in ('hwnd','pid','path')):
                raise ValueError(f'{account} 窗口身份已变化；本功能只更换原窗口内的目标群')
            if same_binding(old,record):raise ValueError(f'{account} 本批已使用这两个目标群，无需重复更换')
            self.plans.validate_batch_plan(plan)
            batch=self.store.batch(plan['batch_id'])
            if batch['status'] not in ('active','waiting'):
                raise ValueError(f'{account} 批次已完成、暂停或待核查，不能更换本批目标')
            held=self.db.execute("SELECT 1 FROM items WHERE batch_id=? AND status IN ('reserved','uncertain')",
                (plan['batch_id'],)).fetchone()
            if held:raise ValueError(f'{account} 仍有未确认联系人，先核查，不更换目标')
            rows.append({'account':account,'batch_id':plan['batch_id'],'numbers':plan['numbers'],
                'batch_status':batch['status'],'old_record':old,'new_record':record,'old_plan':plan,
                'old_selection_states':self.plans.states(account),
                'old_navigation_states':self.bindings.navigation_states(account)})
        return {'scope':'batch_target_replacement','state':'preview','rows':rows,
            'next_number':self.store.next_contact_number(),'contact_database_updated':False,
            'final_invite_clicked':False,'invite_result_inferred':False}

    def confirm(self,preview,live_records):
        """UI thread only, after two fresh live reads of each selected group pair."""
        if preview.get('scope')!='batch_target_replacement' or preview.get('state')!='preview':
            raise ValueError('请先预览更换本批目标群')
        original=[r['new_record'] for r in preview['rows']]
        live=navigation_entries(copy.deepcopy(live_records))
        if len(live)!=len(original) or any(not same_binding(a,b) for a,b in zip(original,live)):
            raise ValueError('现场群名、顺序或窗口已变化，请重新扫描预览')
        self.store._begin()
        try:
            if self.preview(original)!=preview:raise ValueError('批次、联系人或旧群状态已变化，请重新预览')
            result=copy.deepcopy(preview);result.update(state='saved',read_only=False,saved=[])
            for row in result['rows']:
                account=row['account'];plan=copy.deepcopy(row['old_plan'])
                plan.update(plan_id=uuid.uuid4().hex,record=copy.deepcopy(row['new_record']),
                    retargeted_from_plan_id=row['old_plan']['plan_id'])
                plan.pop('group1_selection',None)
                self.bindings._write_record(row['new_record'])
                encoded=json.dumps(plan,ensure_ascii=False)
                self.db.execute('UPDATE pinned_member_plans SET plan_json=? WHERE account=?',(encoded,account))
                self.db.execute('UPDATE pinned_batch_member_plans SET plan_json=? WHERE batch_id=? AND account=?',
                    (encoded,row['batch_id'],account))
                # Full states are archived in this report; historical runs and
                # immutable addition-queue bindings are kept in their tables.
                self.db.execute('DELETE FROM pinned_member_states WHERE account=?',(account,))
                self.db.execute('DELETE FROM pinned_group_navigation_states WHERE account=?',(account,))
                result['saved'].append({'account':account,'batch_id':row['batch_id'],
                    'plan_id':plan['plan_id'],'numbers':plan['numbers'],
                    'targets':row['new_record']['targets'],'groups_require_new_selection':[1,2]})
                self.store._event('pinned_batch_targets_replaced',batch_id=row['batch_id'],
                    detail=json.dumps({'account':account,'old_plan_id':row['old_plan']['plan_id'],
                        'new_plan_id':plan['plan_id'],'old_groups':[t['name'] for t in row['old_record']['targets']],
                        'new_groups':[t['name'] for t in row['new_record']['targets']],
                        'numbers':plan['numbers'],'invite_result_inferred':False},ensure_ascii=False))
            self.db.execute('INSERT INTO pinned_target_replacements(report_json) VALUES(?)',
                (json.dumps(result,ensure_ascii=False),))
            self.db.commit();return result
        except Exception:self.db.rollback();raise

    def latest(self):
        row=self.db.execute('SELECT report_json FROM pinned_target_replacements ORDER BY id DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None
