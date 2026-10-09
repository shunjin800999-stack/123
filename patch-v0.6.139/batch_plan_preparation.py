"""Preview actual per-account success lists, then save all ready rows atomically."""
import copy
import json

from pinned_members import same_binding


class BatchPlanPreparation:
    def __init__(self,store,plans,bindings,addition_queue):
        self.store=store;self.plans=plans;self.bindings=bindings;self.queue=addition_queue
        self.db=store.db
        self.db.execute('CREATE TABLE IF NOT EXISTS batch_plan_preparations(id INTEGER PRIMARY KEY,report_json TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS batch_plan_switch_history(id INTEGER PRIMARY KEY,account TEXT NOT NULL,old_batch_id INTEGER NOT NULL,new_batch_id INTEGER NOT NULL,plan_json TEXT NOT NULL,states_json TEXT NOT NULL,saved TEXT DEFAULT CURRENT_TIMESTAMP)')
        self.db.commit()

    def preview(self,queue_id=None):
        snapshot=self.queue.snapshot(queue_id)
        if not snapshot or snapshot['state']!='done' or snapshot['target_mode'] not in ('pinned','contact'):
            raise ValueError('请先完成本次置顶群添加队列；未结束或待核查的队列不能统一生成名单')
        if snapshot['id']!=self.queue.latest():raise ValueError('只能处理当前最新添加队列，不能恢复旧队列名单')
        result={'scope':'batch_plan_preparation','queue_id':snapshot['id'],'state':'preview',
            'read_only':True,'next_number':snapshot['next_number'],'rows':[],
            'contact_database_updated':False,'final_invite_clicked':False}
        for job in snapshot['jobs']:
            if job['state'] not in ('target_reached','paused','no_list','skipped_account'):
                raise ValueError('队列含未结束的账号任务，请先核查')
            account=job['account'];record=self.bindings.queue_binding(account,job['window'])
            if snapshot['target_mode']=='pinned' and (not job['pinned_binding'] or not same_binding(record,job['pinned_binding'])):
                raise ValueError(account+' 当前窗口或目标群与添加队列不一致')
            numbered=self.db.execute('SELECT COUNT(*) FROM items WHERE batch_id=? AND contact_number IS NOT NULL',(job['batch_id'],)).fetchone()[0]
            batch=self.store.batch(job['batch_id']);members=[]
            if numbered:
                batch,members=self.plans.batch_members(job['batch_id'],allow_completed=True,allow_partial=True)
            row={'account':account,'batch_id':job['batch_id'],'record':record,
                'batch_status':batch['status'],'job_state':job['state'],'target':batch['target'],
                'members':members,'numbers':[m['number'] for m in members],
                'held_items':[r[0] for r in self.db.execute("SELECT id FROM items WHERE batch_id=? AND status IN ('reserved','uncertain') ORDER BY id",(job['batch_id'],))],
                'finish_adding':False}
            if batch['status'] in ('completed','selection_done'):row.update(state='completed',reason='本批已完成，保留历史，不重新生成')
            elif not members:row.update(state='empty',reason='本批没有成功联系人，跳过')
            elif batch['status']=='active' and len(members)<batch['target'] and row['held_items']:
                raise ValueError(account+' 尚有未确认任务；不能自动结束剩余添加')
            else:
                previous=self.plans.get(account)
                if (previous and previous.get('source')=='confirmed_batch' and previous['batch_id']!=job['batch_id']
                        and self.store.batch(previous['batch_id'])['status'] not in ('completed','selection_done')):
                    if snapshot['target_mode']!='contact' and not self.db.execute('SELECT 1 FROM selection_carry_batches WHERE batch_id=?',(previous['batch_id'],)).fetchone():raise ValueError(account+' 另一批名单尚未完成，不能覆盖')
                    row['previous_plan']=copy.deepcopy(previous)
                    row['previous_states']=self.plans.states(account)
                archived=self.db.execute('SELECT plan_json FROM pinned_batch_member_plans WHERE batch_id=?',(job['batch_id'],)).fetchone()
                if archived:
                    plan=json.loads(archived[0])
                    if not same_binding(plan['record'],record) or plan['members']!=members:
                        raise ValueError(account+' 已冻结名单与当前目标或成功联系人不一致')
                row.update(state='ready',reason='已确认成功名单可用于两群',
                    finish_adding=batch['status']=='active' and len(members)<batch['target'])
            result['rows'].append(row)
        return copy.deepcopy(result)

    def confirm(self,preview):
        self.store._begin()
        try:
            current=self.preview(preview['queue_id'])
            if current!=preview:raise ValueError('预览后批次、账号或名单已变化，请重新预览；未保存任何账号')
            saved=[]
            for row in current['rows']:
                if row['state']!='ready':continue
                if row.get('previous_plan'):
                    old=row['previous_plan']
                    self.db.execute('INSERT INTO batch_plan_switch_history(account,old_batch_id,new_batch_id,plan_json,states_json) VALUES(?,?,?,?,?)',
                        (row['account'],old['batch_id'],row['batch_id'],json.dumps(old,ensure_ascii=False),json.dumps(row['previous_states'],ensure_ascii=False)))
                    self.db.execute('DELETE FROM pinned_member_states WHERE account=?',(row['account'],))
                    self.store._event('invitation_plan_switched',batch_id=row['batch_id'],detail='旧批名单和选人状态保留历史；未确认旧批邀请；当前切换到最新成功批次')
                plan=self.plans._save_batch_record(row['record'],row['batch_id'],finish_adding=row['finish_adding'])
                saved.append({'account':row['account'],'batch_id':plan['batch_id'],'plan_id':plan['plan_id'],'numbers':plan['numbers']})
            result=copy.deepcopy(current);result.update(state='saved',read_only=False,saved=saved,
                contact_database_updated=any(r['state']=='ready' and r['finish_adding'] for r in current['rows']))
            self.db.execute('INSERT INTO batch_plan_preparations(report_json) VALUES(?)',(json.dumps(result,ensure_ascii=False),))
            self.store._event('batch_plans_prepared',detail=json.dumps({'queue_id':current['queue_id'],'accounts':[r['account'] for r in saved]},ensure_ascii=False))
            self.db.commit();return result
        except Exception:self.db.rollback();raise

    def latest(self):
        row=self.db.execute('SELECT report_json FROM batch_plan_preparations ORDER BY id DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None
