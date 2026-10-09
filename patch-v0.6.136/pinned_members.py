"""Frozen per-account member lists, target navigation and selection; never invite.

SQLite is used only by the UI thread. The worker receives detached snapshots
and journals each completed window before starting the next one.
"""
import copy
from collections import Counter
import json
from pathlib import Path
import uuid

from group_navigation import navigation_entries, same_observed_targets, open_pinned_groups, navigation_verified
from member_batch import select_members_test
from visual_members import member_labels
from window_queue import ready_for_manual_invite
from controls_probe import WindowActionError, MemberSelectionPaused, check_member_pause
from backup_ocr import require_environment


def same_binding(a,b):
    return (a['account']==b['account'] and
        all(a['window'].get(k)==b['window'].get(k) for k in ('hwnd','pid','path'))
        and same_observed_targets(a,b))


def job_matches_plan(job,plan,slot):
    record=plan['record']
    return (job.get('plan_id')==plan['plan_id'] and job.get('account')==record['account']
        and job.get('source')=='confirmed_batch' and job.get('batch_id')==plan.get('batch_id')
        and job.get('slot')==slot and job.get('numbers')==plan['numbers']
        and job.get('members')==plan.get('members') and job.get('target')==record['targets'][slot-1]
        and all((job.get('window') or {}).get(k)==record['window'].get(k) for k in ('hwnd','pid','path')))


def untouched_profile_failure(job,plan,slot):
    """Only the observed initial profile-panel refusal proves no navigation input."""
    if not job_matches_plan(job,plan,slot) or job.get('state')!='review' or 'selection' in job:return False
    nav=job.get('navigation') or {};jobs=nav.get('jobs') or []
    if (nav.get('scope')!='pinned_group_navigation_queue' or nav.get('ok') is not False
            or nav.get('state')!='review' or nav.get('slot')!=slot or len(jobs)!=1
            or any(nav.get(k) is not False for k in ('members_selected','final_invite_clicked','contact_database_updated'))):return False
    row=jobs[0];child=row.get('navigation') or {};record=plan['record'];w=record['window'];g=record['targets'][slot-1]
    return (row.get('account')==record['account'] and row.get('slot')==slot
        and row.get('target')==g and all((row.get('window') or {}).get(k)==w.get(k) for k in ('hwnd','pid','path'))
        and child.get('scope')=='pinned_group_navigation' and child.get('ok') is False
        and child.get('state')=='review' and child.get('stage')=='initial'
        and child.get('actions')==[] and child.get('errors')==[]
        and child.get('window_handle')==w['hwnd'] and child.get('process_id')==w['pid']
        and child.get('slot')==slot and child.get('group_name')==g['name']
        and child.get('group_runtime_id')==g['runtime_id']
        and child.get('error')=='Return to the ordinary chat page and close the profile panel before this test.'
        and all(child.get(k) is False for k in ('members_selected','final_invite_clicked','contact_database_updated')))


def verified_historical_job(job,plan,slot):
    if (not job_matches_plan(job,plan,slot)
            or job.get('state') not in ('selection_finished','waiting_for_manual_invite','confirmed_invited')):return False
    record=plan['record'];selection=job.get('selection') or {};nav=job.get('navigation') or {}
    nav_jobs=nav.get('jobs') or []
    if len(nav_jobs)!=1 or not navigation_verified(nav_jobs[0].get('navigation'),record,slot):return False
    if not ready_for_manual_invite(selection,record['window'],plan['numbers']):return False
    final=selection['final'];dialog=nav_jobs[0]['navigation']['dialog_runtime_id']
    reads=selection.get('final_reads') or []
    if final.get('dialog_runtime_id')!=dialog or len(reads)<2:return False
    for read in reads[-2:]:
        report=read.get('report') or {};analysis=report.get('analysis') or {}
        if (read.get('selection_verified') is not True or report.get('ok') is not True
                or report.get('read_only') is not True or report.get('scope')!='member_visual'
                or report.get('final_invite_clicked') is not False
                or any(report.get(k) is None or report.get(k)!=final.get(k) for k in
                    ('window_handle','process_id','dialog_runtime_id','capture'))
                or analysis.get('usable') is not True or analysis.get('other_header_words')!=[]
                or Counter(analysis.get('selected_numbers',[]))!=Counter(plan['numbers'])):return False
    return True


class PinnedMemberPlans:
    def __init__(self,store):
        self.store=store;self.db=store.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS pinned_member_plans (
                account TEXT PRIMARY KEY, plan_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pinned_member_runs (
                id INTEGER PRIMARY KEY, report_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pinned_member_states (
                account TEXT NOT NULL, slot INTEGER NOT NULL CHECK(slot IN(1,2)),
                job_json TEXT NOT NULL, PRIMARY KEY(account,slot));
            CREATE TABLE IF NOT EXISTS pinned_batch_member_plans (
                batch_id INTEGER PRIMARY KEY REFERENCES batches(id),
                account TEXT NOT NULL, plan_json TEXT NOT NULL);
        ''')
        self.db.commit()

    def ensure_carry_table(self):
        self.db.execute('CREATE TABLE IF NOT EXISTS selection_carry_batches(batch_id INTEGER PRIMARY KEY,account TEXT NOT NULL)')

    def get(self,account):
        row=self.db.execute('SELECT plan_json FROM pinned_member_plans WHERE account=?',(account,)).fetchone()
        if not row:return None
        from selection_scope import plan_allowed
        plan=json.loads(row[0])
        return plan if plan_allowed(self.db,plan) else None

    def save(self,observation,numbers):
        record=navigation_entries([observation])[0];labels=member_labels(numbers)
        from selection_scope import require_record,stamp
        require_record(self.db,record)
        previous=self.get(record['account'])
        if (previous and previous.get('source')=='existing_contact_remarks'
                and same_binding(previous['record'],record) and previous['numbers']==labels):
            return previous
        plan={'plan_id':uuid.uuid4().hex,'record':record,'numbers':labels,
            'source':'existing_contact_remarks','contact_database_updated':False}
        stamp(self.db,plan)
        with self.db:
            self.db.execute('''INSERT INTO pinned_member_plans(account,plan_json) VALUES(?,?)
                ON CONFLICT(account) DO UPDATE SET plan_json=excluded.plan_json''',
                (record['account'],json.dumps(plan,ensure_ascii=False)))
        return copy.deepcopy(plan)

    def batch_members(self,batch_id,*,allow_completed=False,allow_partial=False):
        from selection_scope import require_batch,current_queue
        require_batch(self.db,batch_id)
        batch=self.store.batch(batch_id)
        if batch['status']=='completed' and not allow_completed:raise ValueError('本批已登记完成，不再生成邀请名单')
        self.ensure_carry_table()
        batch_ids=[batch_id]+[r[0] for r in self.db.execute('SELECT c.batch_id FROM selection_carry_batches c JOIN batches b ON b.id=c.batch_id WHERE c.account=? AND c.batch_id<>? AND b.status NOT IN (\'completed\',\'selection_done\')',(batch['account'],batch_id))]
        if current_queue(self.db) is not None:batch_ids=[batch_id]
        frozen=self.db.execute('SELECT plan_json FROM pinned_batch_member_plans WHERE batch_id=?',(batch_id,)).fetchone()
        if frozen:batch_ids=json.loads(frozen[0]).get('batch_ids',batch_ids)
        for bid in batch_ids:require_batch(self.db,bid)
        marks=','.join('?' for _ in batch_ids)
        rows=list(self.db.execute("SELECT * FROM items WHERE batch_id IN ("+marks+") AND contact_number IS NOT NULL AND ((batch_id=? AND batch_id NOT IN (SELECT batch_id FROM selection_carry_batches)) OR status IN ('added','pending_invite','completed')) ORDER BY contact_number",batch_ids+[batch_id]))
        if not rows:raise ValueError('本批没有已登记添加成功的联系人；请先在使用记录登记真实添加结果')
        allowed=('added','pending_invite','completed') if allow_completed else ('added','pending_invite')
        for row in rows:
            if (row['account']!=batch['account'] or row['status'] not in allowed
                    or row['numbering_global'] not in (0,1) or not row['added_at']):
                raise ValueError('本批含未确认或编号异常记录，请核查；不推测联系人实际备注')
            if row['numbering_global']==0 and not self.db.execute(
                    "SELECT 1 FROM events WHERE action='added' AND item_id=? AND batch_id=? AND time=?",
                    (row['id'],batch_id,row['added_at'])).fetchone():
                raise ValueError(f'备注 {row["contact_number"]} 是旧版记录，但缺少对应批次和日期的添加成功事件，请核查')
        members=[{'item_id':r['id'],'number':str(r['contact_number'])} for r in rows]
        member_labels([m['number'] for m in members])
        if batch['status']=='active' and len(members)<batch['target'] and not allow_partial:
            raise ValueError('本批尚未达到计划人数且未结束添加，请先完成添加或记录暂停结果')
        return batch,members

    def save_batch(self,observation,batch_id,*,finish_adding=False):
        record=navigation_entries([observation])[0]
        self.store._begin()
        try:
            plan=self._save_batch_record(record,batch_id,finish_adding=finish_adding)
            self.db.commit();return copy.deepcopy(plan)
        except Exception:self.db.rollback();raise

    def _save_batch_record(self,record,batch_id,*,finish_adding=False):
        """Caller owns the transaction, including bulk all-or-nothing saves."""
        from selection_scope import require_record,stamp
        require_record(self.db,record)
        batch,members=self.batch_members(batch_id,allow_partial=finish_adding)
        if batch['account']!=record['account']:raise ValueError('使用记录当前批次不属于上方账号，请切换到对应批次')
        if (self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contact_queue_jobs'").fetchone()
                and 'binding_json' in {r['name'] for r in self.db.execute('PRAGMA table_info(contact_queue_jobs)')}
                and 'target_mode' in {r['name'] for r in self.db.execute('PRAGMA table_info(contact_queues)')}):
            source_batches={self.db.execute('SELECT batch_id FROM items WHERE id=?',(m['item_id'],)).fetchone()[0] for m in members}
            for source_batch in source_batches:
                queued=self.db.execute("""SELECT j.binding_json FROM contact_queue_jobs j
                    JOIN contact_queues q ON q.id=j.queue_id
                    WHERE j.batch_id=? AND q.target_mode='pinned' ORDER BY j.id DESC LIMIT 1""",(source_batch,)).fetchone()
                if queued and (not json.loads(queued[0]) or not same_binding(json.loads(queued[0]),record)):
                    raise ValueError('成功名单包含绑定另一窗口或置顶群的旧批次，请核查；保留记录，不能替换本批目标')
        if finish_adding and batch['status']=='active':
            held=self.db.execute("SELECT COUNT(*) FROM items WHERE batch_id=? AND status IN ('reserved','uncertain')",(batch_id,)).fetchone()[0]
            if held:raise ValueError('本批仍有未确认的占用任务，请先核查或记录真实暂停结果')
            self.db.execute("UPDATE batches SET status='waiting' WHERE id=?",(batch_id,))
            self.db.execute("UPDATE items SET status='pending_invite' WHERE batch_id=? AND status='added'",(batch_id,))
            self.store._event('pinned_batch_adding_finished',batch_id=batch_id,
                detail=f'人工结束本批剩余添加，以 {len(members)} 位成功记录生成邀请名单；未邀请')
        row=self.db.execute('SELECT plan_json FROM pinned_batch_member_plans WHERE batch_id=?',(batch_id,)).fetchone()
        if row:
            plan=json.loads(row[0])
            if not same_binding(plan['record'],record) or plan['members']!=members:
                raise ValueError('本批两群名单已冻结，目标或成功联系人发生变化；不能覆盖已执行的名单')
        else:
            plan={'plan_id':uuid.uuid4().hex,'record':record,'source':'confirmed_batch',
                'batch_id':batch_id,'batch_ids':sorted({self.db.execute('SELECT batch_id FROM items WHERE id=?',(m['item_id'],)).fetchone()[0] for m in members}),'members':members,'numbers':[m['number'] for m in members],
                'legacy_item_ids':[r['id'] for r in self.db.execute(
                    'SELECT id FROM items WHERE batch_id=? AND contact_number IS NOT NULL AND numbering_global=0 ORDER BY contact_number',(batch_id,))],
                'contact_database_updated':False}
            stamp(self.db,plan)
            self.db.execute('INSERT INTO pinned_batch_member_plans(batch_id,account,plan_json) VALUES(?,?,?)',
                (batch_id,record['account'],json.dumps(plan,ensure_ascii=False)))
            self.store._event('pinned_batch_plan_saved',batch_id=batch_id,
                detail=f'本批 {len(members)} 位已确认联系人用于两个置顶群；未选择或邀请')
        self.db.execute('''INSERT INTO pinned_member_plans(account,plan_json) VALUES(?,?)
            ON CONFLICT(account) DO UPDATE SET plan_json=excluded.plan_json''',
            (record['account'],json.dumps(plan,ensure_ascii=False)))
        return copy.deepcopy(plan)

    def validate_batch_plan(self,plan,*,allow_completed=False):
        from selection_scope import plan_allowed
        if not plan_allowed(self.db,plan):raise ValueError('旧添加计划的选人名单已失效，请生成本次添加名单')
        row=self.db.execute('SELECT plan_json FROM pinned_batch_member_plans WHERE batch_id=?',(plan['batch_id'],)).fetchone()
        if not row or json.loads(row[0])!= {k:v for k,v in plan.items() if k!='group1_selection'}:
            raise ValueError('数据库名单与冻结记录不一致，请核查')
        batch,members=self.batch_members(plan['batch_id'],allow_completed=allow_completed)
        if batch['account']!=plan['record']['account'] or members!=plan['members']:
            raise ValueError('本批成功联系人发生变化，停止，不重新分配编号')

    def confirm_group_invited(self,account,slot):
        if type(slot) is not int or slot not in (1,2):raise ValueError('目标群只能是 1 或 2')
        self.store._begin()
        try:
            plan=self.get(account)
            if not plan or plan.get('source')!='confirmed_batch':raise ValueError('手填测试名单不能登记数据库邀请结果')
            self.validate_batch_plan(plan,allow_completed=True)
            states=self.states(account);job=self.confirmation_job(account,slot)
            if (job.get('plan_id')!=plan['plan_id'] or job.get('numbers')!=plan['numbers']
                    or job.get('state') not in ('selection_finished','waiting_for_manual_invite','confirmed_invited')):
                raise ValueError('请先完成本批名单在这个群的搜索选人核对')
            if job['state']=='confirmed_invited':
                self.db.commit();return {'already_confirmed':True,'all_groups_confirmed':all(
                    states.get(s,{}).get('plan_id')==plan['plan_id'] and states.get(s,{}).get('state')=='confirmed_invited' for s in (1,2)),
                    'held_items':self.db.execute("SELECT COUNT(*) FROM items WHERE batch_id=? AND status IN ('reserved','uncertain')",(plan['batch_id'],)).fetchone()[0]}
            if not ready_for_manual_invite(job.get('selection') or {},job['window'],plan['numbers']):
                raise ValueError('本群选人报告不完整，不能确认成功')
            if job.get('selection_evidence_recovered_from_run') is not None:
                self.store._event('pinned_selection_history_recovered',batch_id=plan['batch_id'],
                    detail=json.dumps({'account':account,'slot':slot,
                        'from_run':job['selection_evidence_recovered_from_run'],
                        'untouched_later_runs':job['untouched_later_run_ids']},ensure_ascii=False))
            job.update(state='confirmed_invited',manual_invite_confirmed=True)
            self.db.execute('UPDATE pinned_member_states SET job_json=? WHERE account=? AND slot=?',
                (json.dumps(job,ensure_ascii=False),account,slot))
            states[slot]=job
            complete=all(states.get(s,{}).get('plan_id')==plan['plan_id'] and states.get(s,{}).get('state')=='confirmed_invited' for s in (1,2))
            ids=[m['item_id'] for m in plan['members']]
            for item_id in ids:
                self.db.execute('UPDATE items SET status=?,updated=strftime(\'%Y-%m-%d %H:%M:%f\',\'now\') WHERE id=?',
                    ('completed' if complete else 'pending_invite',item_id))
            held=self.db.execute("SELECT COUNT(*) FROM items WHERE batch_id=? AND status IN ('reserved','uncertain')",(plan['batch_id'],)).fetchone()[0]
            if not held:
                self.db.execute('UPDATE batches SET status=? WHERE id=?',('completed' if complete else 'waiting',plan['batch_id']))
            self.store._event('pinned_group_manual_invite_confirmed',batch_id=plan['batch_id'],
                detail=json.dumps({'slot':slot,'plan_id':plan['plan_id'],'all_groups_confirmed':complete},ensure_ascii=False))
            self.db.commit();return {'already_confirmed':False,'all_groups_confirmed':complete,'held_items':held}
        except Exception:self.db.rollback();raise

    def confirmation_job(self,account,slot):
        """Read matching evidence; history recovery never confirms membership itself."""
        if type(slot) is not int or slot not in (1,2):raise ValueError('目标群只能是 1 或 2')
        plan=self.get(account)
        if not plan or plan.get('source')!='confirmed_batch':raise ValueError('手填测试名单不能登记数据库邀请结果')
        self.validate_batch_plan(plan,allow_completed=True)
        current=self.states(account).get(slot) or {}
        if current.get('plan_id')==plan['plan_id'] and current.get('state') in ('selection_finished','waiting_for_manual_invite','confirmed_invited'):
            return copy.deepcopy(current)
        if untouched_profile_failure(current,plan,slot):
            later=[]
            for row in self.db.execute('SELECT id,report_json FROM pinned_member_runs ORDER BY id DESC'):
                report=json.loads(row['report_json'])
                jobs=[j for j in report.get('jobs',[]) if j.get('account')==account and j.get('slot')==slot]
                if not jobs:continue
                if (len(jobs)!=1 or report.get('scope')!='pinned_member_selection_queue'
                        or report.get('final_invite_clicked') is not False
                        or report.get('contact_database_updated') is not False):break
                job=jobs[0]
                if not later and job!=current:break
                if untouched_profile_failure(job,plan,slot):
                    later.append(row['id']);continue
                if later and verified_historical_job(job,plan,slot):
                    recovered=copy.deepcopy(job)
                    recovered.update(selection_evidence_recovered_from_run=row['id'],untouched_later_run_ids=later)
                    return recovered
                break  # Any later input, unknown outcome or identity change blocks recovery.
        raise ValueError('请先完成本批名单在这个群的搜索选人核对')

    def states(self,account):
        return {r['slot']:json.loads(r['job_json']) for r in self.db.execute(
            'SELECT slot,job_json FROM pinned_member_states WHERE account=?',(account,))}

    def entries(self,observations,slot):
        if type(slot) is not int or slot not in (1,2):raise ValueError('目标群只能是 1 或 2')
        records=navigation_entries(observations);entries=[]
        for record in records:
            plan=self.get(record['account'])
            if not plan:raise ValueError(record['account']+' 尚未保存本次两群备注名单')
            if not same_binding(record,plan['record']):
                raise ValueError(record['account']+' 的窗口或目标群已变化，请重新保存本次两群名单')
            # History records what happened; the user chooses which group to
            # run, including repeating a group or starting with group two.
            if plan.get('source')=='confirmed_batch':self.validate_batch_plan(plan,allow_completed=True)
            entries.append(plan)
        return entries

    def save_run(self,result):
        if (result.get('scope')!='pinned_member_selection_queue'
                or result.get('final_invite_clicked') is not False
                or result.get('contact_database_updated') is not False):raise ValueError('不是两群选人报告')
        preserved=[]
        with self.db:
            self.db.execute('INSERT INTO pinned_member_runs(report_json) VALUES(?)',
                (json.dumps(result,ensure_ascii=False),))
            for job in result['jobs']:
                if job['state'] not in ('selection_finished','waiting_for_manual_invite','review','paused'):continue
                plan=self.get(job['account']);previous=self.states(job['account']).get(job['slot']) or {}
                if job['state']=='selection_finished' and (not plan or job.get('plan_id')!=plan['plan_id'] or not ready_for_manual_invite(job.get('selection') or {},plan['record']['window'],plan['numbers'])):
                    raise ValueError('选人完成缺少对应计划和核验证据')
                if (plan and plan.get('source')=='confirmed_batch'
                        and untouched_profile_failure(job,plan,job['slot'])
                        and verified_historical_job(previous,plan,job['slot'])):
                    preserved.append({'account':job['account'],'slot':job['slot']})
                    self.store._event('pinned_selection_evidence_preserved',batch_id=plan['batch_id'],
                        detail=json.dumps(preserved[-1],ensure_ascii=False))
                    continue
                self.db.execute('''INSERT INTO pinned_member_states(account,slot,job_json) VALUES(?,?,?)
                    ON CONFLICT(account,slot) DO UPDATE SET job_json=excluded.job_json''',
                    (job['account'],job['slot'],json.dumps(job,ensure_ascii=False)))
                if job['state']=='selection_finished' and plan and plan.get('source')=='confirmed_batch':
                    states=self.states(job['account'])
                    if all(states.get(slot,{}).get('plan_id')==plan['plan_id'] and
                           states[slot].get('state') in ('selection_finished','waiting_for_manual_invite','confirmed_invited')
                           for slot in (1,2)):

                        for bid in plan.get('batch_ids',[plan['batch_id']]):
                            self.db.execute("UPDATE batches SET status='selection_done',reason='两群选人任务完成；手动邀请结果不记录' WHERE id=? AND status NOT IN ('completed','selection_done')",(bid,))
        return preserved

    def finish_existing_selections(self):
        """Finish proven old selection tasks without asserting manual invitation."""
        with self.db:
            for row in self.db.execute('SELECT account FROM pinned_member_plans').fetchall():
                account=row[0];plan=self.get(account);states=self.states(account)
                if not plan:continue
                if not all(states.get(slot,{}).get('plan_id')==plan['plan_id'] and
                    states[slot].get('state') in ('selection_finished','waiting_for_manual_invite') and
                    ready_for_manual_invite(states[slot].get('selection') or {},plan['record']['window'],plan['numbers'])
                    for slot in (1,2)):continue
                for slot in (1,2):
                    job=states[slot];job['state']='selection_finished'
                    self.db.execute('UPDATE pinned_member_states SET job_json=? WHERE account=? AND slot=?',
                                    (json.dumps(job,ensure_ascii=False),account,slot))
                if plan.get('source')=='confirmed_batch':
                    for bid in plan.get('batch_ids',[plan['batch_id']]):
                        self.db.execute("UPDATE batches SET status='selection_done',reason='两群选人任务已完成，邀请结果不记录' WHERE id=? AND status NOT IN ('completed','selection_done')",(bid,))

    def latest(self):
        row=self.db.execute('SELECT report_json FROM pinned_member_runs ORDER BY id DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None


def select_pinned_member_queue(plans,slot,report_path,progress=None,*,prepare_pages=False,contacts_by_account=None,fast_visible=False,target_only=False,should_stop=None):
    if target_only and not fast_visible:raise ValueError('本次目标模式需要快速逐位选择')
    if type(slot) is not int or slot not in (1,2):raise ValueError('目标群只能是 1 或 2')
    if type(prepare_pages) is not bool:raise ValueError('页面整理模式无效')
    entries=copy.deepcopy(list(plans))
    records=navigation_entries([p['record'] for p in entries])
    for plan in entries:
        plan['numbers']=member_labels(plan['numbers'])
        if not plan.get('plan_id') or plan.get('source') not in ('existing_contact_remarks','confirmed_batch'):raise ValueError('缺少冻结的本次名单')
        if plan['source']=='confirmed_batch' and (type(plan.get('batch_id')) is not int or plan['batch_id']<=0
                or not isinstance(plan.get('members'),list) or [m.get('number') for m in plan['members']]!=plan['numbers']
                or any(type(m.get('item_id')) is not int or m['item_id']<=0 for m in plan['members'])
                or len({m['item_id'] for m in plan['members']})!=len(plan['members'])):
            raise ValueError('数据库名单的联系人记录与数字备注不一致')
    path=Path(report_path).resolve()
    if path.suffix.lower()!='.json':raise ValueError('两群选人报告请保存为 JSON')
    files=path.parent/(path.stem+'_files_'+uuid.uuid4().hex[:8])
    jobs=[{'index':i,'plan_id':p['plan_id'],'account':r['account'],'window':r['window'],
        'target':r['targets'][slot-1],'slot':slot,'numbers':p['numbers'],
        'source':p['source'],'batch_id':p.get('batch_id'),'members':p.get('members',[]),
        'legacy_item_ids':p.get('legacy_item_ids',[]),
        'state':'not_started'} for i,(p,r) in enumerate(zip(entries,records),1)]
    result={'ok':False,'scope':'pinned_member_selection_queue','state':'preparing','slot':slot,
        'jobs':jobs,'selection_verified':False,'final_invite_clicked':False,'contact_database_updated':False,
        'account_identity_verified':False,'report_path':str(path),'files_directory':str(files),'reason':''}
    def save():
        temp=path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    def announce(message):
        if progress:progress(message)
    save();current=None
    try:
        require_environment()
        files.mkdir()
        for record,job in zip(records,jobs):
            current=job;check_member_pause(should_stop);result['state']='running'
            if prepare_pages:
                from group_cleanup import prepare_group_page
                job['state']='cleaning';save()
                announce(job['account']+'：先整理页面，最终 Add 仍由你点击。')
                job['cleanup']=prepare_group_page(record,(contacts_by_account or {}).get(record['account'],[]),
                    files/f'window{job["index"]:02d}_cleanup.json')
            check_member_pause(should_stop)
            job['state']='opening';save()
            prefix=f'{job["account"]} · 群 {slot}「{job["target"]["name"]}」'
            def navigation_progress(message):
                announce(prefix+'：添加成员页面已打开，准备选人。' if '继续下一窗口' in message else message)
            nav=open_pinned_groups([record],slot,files/f'window{job["index"]:02d}_navigation.json',navigation_progress)
            job['navigation']=nav
            child=(nav.get('jobs') or [{}])[0].get('navigation') or {}
            if nav.get('ok') is not True or not navigation_verified(child,record,slot):
                raise ValueError(prefix+' 未打开并核对目标群：'+nav.get('reason','请查看报告'))
            job['state']='selecting';save()
            announce(prefix+'：开始按本次名单搜索、选人；最终 Add 由你点击。')
            check_member_pause(should_stop)
            options={'fast_visible':True,'should_stop':should_stop} if fast_visible else {}
            if target_only:options['target_only']=True
            if should_stop is not None:options['should_stop']=should_stop
            selected=select_members_test(copy.deepcopy(record['window']),
                files/f'window{job["index"]:02d}_selection.json',job['numbers'][:],
                lambda message:announce(prefix+'：'+message),expected_dialog_id=child['dialog_runtime_id'],**options)
            job['selection']=selected
            if selected.get('state')=='paused':raise MemberSelectionPaused(selected.get('reason','已暂停选人'))
            if (not ready_for_manual_invite(selected,record['window'],job['numbers'])
                    or (selected.get('final') or {}).get('dialog_runtime_id')!=child['dialog_runtime_id']):
                raise ValueError(prefix+' 名单未完整核对：'+selected.get('reason','请查看报告'))
            job['state']='selection_finished' if fast_visible else 'waiting_for_manual_invite';save()
            # Save A before moving to B. No human wait or inferred invite success.
            announce(prefix+'：全部已选，保留在 Add 前；继续下一窗口。')
        result.update(ok=True,state='selection_finished' if fast_visible else 'waiting_for_manual_invite',selection_verified=True,
            reason='所选账号选人任务已完成；Add由你手动操作，助手无需记录邀请结果。')
    except Exception as error:
        if current:
            if isinstance(error,WindowActionError) and current['state']=='cleaning':current['cleanup']=error.report
            current['state']='paused' if (isinstance(error,MemberSelectionPaused) or (should_stop is not None and should_stop())) else 'review'
        result.update(state='paused' if (isinstance(error,MemberSelectionPaused) or (should_stop is not None and should_stop())) else 'review',reason=str(error)+'；停止后续窗口，已核对窗口的选择保留，不自动重试。')
    save();return result
