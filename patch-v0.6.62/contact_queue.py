"""Durable account queue. Guarded Create is opt-in; group Add stays manual."""
import json
from pathlib import Path

from username_contact import username_profile_verified
from controls_probe import verify_contact_profile
from contact_open import form_open_verified
from contact_submit import submission_payload, submission_verified
from contact_profile_close import previous_profile_verified, profile_close_verified
from pinned_scan import path_key
from window_queue import queue_windows


LIMIT_TEXTS = frozenset((
    'Too many tries. Please try again later.',
    'Too many attempts. Please try again later.',
    '操作过于频繁，请稍后再试。',
    '尝试次数过多，请稍后再试。',
    '尝试次数太多，请稍后再试。',
))


def addition_windows(windows):
    """Contact queues support one account as well as existing multi-window checks."""
    from groups import valid_window
    windows=list(windows)
    if len(windows)==1:
        if not valid_window(windows[0]):raise ValueError('请选择已还原的 Telegram.exe 窗口')
        return [dict(windows[0])]
    return queue_windows(windows)


def reserved_phone_fill(store,batch_id,window,binding):
    """Read-only snapshot for one manually reserved phone; never reserve or submit."""
    from groups import valid_window
    if not valid_window(window):raise ValueError('请选择已还原的 Telegram.exe 窗口')
    batch=store.batch(batch_id)
    if batch['status']!='active':raise ValueError('当前批次已暂停或结束，请先核查，不自动重试')
    held=[r for r in store.rows() if r['batch_id']==batch_id and r['status'] in ('reserved','uncertain')]
    if len(held)!=1 or held[0]['status']!='reserved' or held[0]['source']!='phone':
        raise ValueError('当前批次需要恰好一位已占用、尚未添加的手机号用户')
    item=held[0]
    if item['account']!=batch['account'] or item['contact_number'] is not None:
        raise ValueError('占用记录与账号或编号状态不一致，请核查')
    if (not binding or binding.get('account')!=batch['account'] or
            any(binding.get('window',{}).get(k)!=window.get(k) for k in ('hwnd','pid','path'))):
        raise ValueError('当前批次账号与所选窗口没有对应的置顶群绑定，请先选择正确账号窗口并识别置顶群')
    if (store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pinned_batch_member_plans'").fetchone()
            and store.db.execute('SELECT 1 FROM pinned_batch_member_plans WHERE batch_id=?',(batch_id,)).fetchone()):
        raise ValueError('当前批次两群名单已冻结，不能继续试填新联系人')
    number=store.next_contact_number()
    if not 1<=number<=999999:raise ValueError('全局编号已用完，停止试填')
    return {'batch_id':batch_id,'account':batch['account'],'item':dict(item),
        'window':dict(window),'preview_number':number}


def complete_report(report, window):
    return (report.get('ok') is True and report.get('read_only') is True
        and report.get('window_handle') == window['hwnd']
        and report.get('process_id') == window['pid']
        and bool(report.get('scope_runtime_id'))
        and report.get('truncated') is False and report.get('errors') == [])


def awaiting_form_window(report,window):
    """A capped ordinary chat scan only proves passive waiting, never an outcome."""
    return (report.get('ok') is True and report.get('read_only') is True
        and report.get('scope')=='window' and report.get('scope_class')=='class MainWindow'
        and report.get('window_handle')==window['hwnd'] and report.get('process_id')==window['pid']
        and bool(report.get('scope_runtime_id')) and type(report.get('truncated')) is bool
        and report.get('errors')==[])


def restriction_evidence(report, window):
    if not complete_report(report, window) or report.get('scope') not in ('dialog', 'contact_dialog'):
        return None
    if report.get('scope_class') != 'class Ui::BoxLayerWidget':
        return None
    texts = [' '.join(str(row.get('name') or '').split()) for row in report.get('controls', [])
        if row.get('type') == 'Text' and row.get('visible') is True]
    buttons = [row for row in report.get('controls', []) if row.get('type') == 'Button'
        and row.get('visible') is True and row.get('enabled') is True
        and row.get('name') in ('OK', 'Ok', '确定', '确认', '关闭', 'Close')]
    # Full labels only: chat quotations, substrings and vague failure words do not count.
    matches = sorted(set(texts) & LIMIT_TEXTS)
    if len(matches) != 1 or not buttons:
        return None
    return {'text': matches[0], 'runtime_id': report['scope_runtime_id']}


def unregistered_evidence(reports,window):
    # Observed AddContactBox result has no exposed body text; require its complete
    # structural signature twice, never a button substring in an arbitrary page.
    if len(reports)!=2 or not all(complete_report(r,window) for r in reports):return None
    proofs=[]
    for r in reports:
        if r.get('scope')!='contact_dialog' or r.get('scope_class')!='class Ui::BoxLayerWidget':return None
        rows=[c for c in r['controls'] if c.get('visible') is True]
        contacts=[c for c in rows if c.get('class_name')=='class AddContactBox' and c.get('type')=='Window']
        titles=[c for c in rows if c.get('type')=='Text' and c.get('name') in ('New Contact','Novo Contato')]
        buttons=[c for c in rows if c.get('type')=='Button']
        if (len(contacts)!=1 or len(titles)!=1 or len(buttons)!=1
                or (titles[0].get('name'),buttons[0].get('name')) not in (('New Contact','Try someone else'),('Novo Contato','Tentar outro')) or buttons[0].get('enabled') is not True
                or buttons[0].get('class_name')!='class Ui::RoundButton'
                or any(c.get('type')=='Edit' for c in rows)):return None
        proofs.append({'runtime_id':r['scope_runtime_id'],'button':buttons[0]['name']})
    return proofs[0] if proofs[0]==proofs[1] else None


def confirmed_observation(reports, window, item, number, *, single_profile=False):
    if item['source']=='username' and len(reports) in (1,2):
        if (all(username_profile_verified(r,window,item['value'],number) for r in reports)
                and all(r['scope_runtime_id']==reports[0]['scope_runtime_id'] for r in reports)):
            return {'outcome':'added','reason':'用户名、数字备注及编辑／删除状态核验通过'}
    if single_profile and len(reports)==1 and complete_report(reports[0],window):
        if item["source"]=="phone" and verify_contact_profile(reports[0],item["value"],str(number))["verified"]:
            return {"outcome":"added","reason":"一次完整只读资料页核验通过"}
    if len(reports) != 2 or not all(complete_report(r, window) for r in reports):
        return None
    first, second = reports
    if first['scope_runtime_id'] != second['scope_runtime_id'] or first['scope'] != second['scope']:
        return None
    limits = [restriction_evidence(r, window) for r in reports]
    if limits[0] and limits[0] == limits[1]:
        return {'outcome': 'restriction', 'reason': limits[0]['text']}
    if item['source'] == 'phone' and all(verify_contact_profile(r, item['value'], str(number))['verified'] for r in reports):
        return {'outcome': 'added', 'reason': '两次只读资料页核验通过'}
    return None


class ContactQueue:
    def __init__(self, store, *, group_catalog=None, pinned_groups=None):
        self.store = store
        self.group_catalog = group_catalog
        self.pinned_groups = pinned_groups
        self.db = store.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS contact_queues (
                id INTEGER PRIMARY KEY, state TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')));
            CREATE TABLE IF NOT EXISTS contact_queue_jobs (
                id INTEGER PRIMARY KEY, queue_id INTEGER NOT NULL REFERENCES contact_queues(id),
                position INTEGER NOT NULL, batch_id INTEGER NOT NULL REFERENCES batches(id),
                window_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                item_id INTEGER REFERENCES items(id), preview_number INTEGER,
                reason TEXT NOT NULL DEFAULT '', UNIQUE(queue_id,position));
            CREATE TABLE IF NOT EXISTS contact_queue_reads (
                queue_id INTEGER PRIMARY KEY REFERENCES contact_queues(id),
                job_id INTEGER NOT NULL REFERENCES contact_queue_jobs(id),
                captured TEXT NOT NULL, reports_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS contact_queue_openings (
                queue_id INTEGER NOT NULL REFERENCES contact_queues(id),
                job_id INTEGER NOT NULL REFERENCES contact_queue_jobs(id),
                item_id INTEGER NOT NULL REFERENCES items(id),
                started TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),
                report_json TEXT, error TEXT,
                PRIMARY KEY(job_id,item_id));
            CREATE TABLE IF NOT EXISTS contact_queue_submissions (
                queue_id INTEGER NOT NULL REFERENCES contact_queues(id),
                job_id INTEGER NOT NULL REFERENCES contact_queue_jobs(id),
                item_id INTEGER NOT NULL REFERENCES items(id),
                number INTEGER NOT NULL, fill_json TEXT NOT NULL,
                started TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),
                report_json TEXT, error TEXT, PRIMARY KEY(job_id,item_id));
            CREATE TABLE IF NOT EXISTS contact_queue_profile_recoveries (
                queue_id INTEGER NOT NULL,job_id INTEGER NOT NULL,item_id INTEGER NOT NULL,
                number INTEGER NOT NULL,reads_json TEXT NOT NULL,PRIMARY KEY(job_id,item_id));
            CREATE TABLE IF NOT EXISTS contact_queue_unregistered (
                queue_id INTEGER NOT NULL, job_id INTEGER NOT NULL, item_id INTEGER NOT NULL,
                number INTEGER NOT NULL, dialog_id TEXT NOT NULL, reads_json TEXT NOT NULL,
                recovery INTEGER NOT NULL, report_json TEXT, error TEXT,
                PRIMARY KEY(job_id,item_id));
            CREATE TABLE IF NOT EXISTS contact_queue_profile_closings (
                queue_id INTEGER NOT NULL REFERENCES contact_queues(id),
                job_id INTEGER NOT NULL REFERENCES contact_queue_jobs(id),
                item_id INTEGER NOT NULL REFERENCES items(id), previous_item_id INTEGER NOT NULL,
                previous_number INTEGER NOT NULL, previous_phone TEXT NOT NULL,
                profile_runtime_id TEXT NOT NULL, reads_json TEXT NOT NULL,
                started TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),
                report_json TEXT, error TEXT, PRIMARY KEY(job_id,item_id));
        ''')
        columns={r['name'] for r in self.db.execute('PRAGMA table_info(contact_queue_jobs)')}
        if 'targets_json' not in columns:
            with self.db:self.db.execute("ALTER TABLE contact_queue_jobs ADD COLUMN targets_json TEXT NOT NULL DEFAULT '[]'")
        if 'binding_json' not in columns:
            with self.db:self.db.execute("ALTER TABLE contact_queue_jobs ADD COLUMN binding_json TEXT NOT NULL DEFAULT 'null'")
        if 'fill_json' not in columns:
            with self.db:self.db.execute('ALTER TABLE contact_queue_jobs ADD COLUMN fill_json TEXT')
        if 'lookup_failures' not in columns:
            with self.db:self.db.execute('ALTER TABLE contact_queue_jobs ADD COLUMN lookup_failures INTEGER NOT NULL DEFAULT 0')
        queue_columns={r['name'] for r in self.db.execute('PRAGMA table_info(contact_queues)')}
        if 'target_mode' not in queue_columns:
            with self.db:self.db.execute("ALTER TABLE contact_queues ADD COLUMN target_mode TEXT NOT NULL DEFAULT 'catalog'")
        if 'auto_submit' not in queue_columns:
            with self.db:self.db.execute('ALTER TABLE contact_queues ADD COLUMN auto_submit INTEGER NOT NULL DEFAULT 0')
        if 'auto_close_profile' not in queue_columns:
            with self.db:self.db.execute('ALTER TABLE contact_queues ADD COLUMN auto_close_profile INTEGER NOT NULL DEFAULT 0')
        if 'contact_source' not in queue_columns:
            with self.db:self.db.execute("ALTER TABLE contact_queues ADD COLUMN contact_source TEXT NOT NULL DEFAULT 'phone'")
        if 'start_username_seq' not in queue_columns:
            with self.db:self.db.execute('ALTER TABLE contact_queues ADD COLUMN start_username_seq INTEGER NOT NULL DEFAULT 1')
        if 'start_phone_seq' not in queue_columns:
            with self.db:self.db.execute('ALTER TABLE contact_queues ADD COLUMN start_phone_seq INTEGER NOT NULL DEFAULT 1')
        # An interrupted fill/submit cannot safely be resumed as a fresh attempt.
        with self.db:
            self.db.execute("UPDATE contact_queues SET state='review',reason='程序曾中断；核查已占用联系人和表单后再建立队列' WHERE state='running'")

    def latest(self, *, include_cleared=False):
        row = self.db.execute('SELECT id FROM contact_queues '+
            ('' if include_cleared else "WHERE state<>'cleared' ")+'ORDER BY id DESC LIMIT 1').fetchone()
        return row[0] if row else None

    def clear(self):
        """Retire queue rows without deleting jobs, observations, held users or numbers."""
        self.store._begin()
        try:
            if self.db.execute("SELECT 1 FROM contact_queues WHERE state='running'").fetchone():
                raise ValueError('请先点击“停止队列”，等待当前界面操作结束后再清空')
            ids=[r[0] for r in self.db.execute("SELECT id FROM contact_queues WHERE state<>'cleared' ORDER BY id")]
            self.db.execute("""UPDATE contact_queues SET state='cleared',
                reason=CASE WHEN reason='' THEN '用户清空账号队列；原始记录保留'
                ELSE reason||'；用户清空账号队列，原始记录保留' END WHERE state<>'cleared'""")
            if ids:self.store._event('contact_queue_cleared',detail=json.dumps({'queue_ids':ids,'items_released':False,'numbers_reset':False},ensure_ascii=False))
            held=[dict(r) for r in self.db.execute("SELECT * FROM items WHERE status IN ('reserved','uncertain') ORDER BY id")]
            self.db.commit()
            return {'cleared_queue_ids':ids,'held_items':held,'next_number':self.store.next_contact_number()}
        except Exception:
            self.db.rollback()
            raise

    def restore_waiting(self,queue_id):
        """Copy an unfilled plan to new job IDs; old workers can never target it."""
        self.store._begin()
        try:
            if self.db.execute("SELECT 1 FROM contact_queues WHERE state IN ('running','configured')").fetchone():
                raise ValueError('已有待开始或正在执行的队列，不能重复恢复')
            old=self.snapshot(queue_id)
            if not old or old['state'] not in ('cleared','stopped','review') or old['target_mode']!='pinned':
                raise ValueError('只能恢复已清空或停止的置顶群手机号待填写队列')
            waiting=[]
            for job in old['jobs']:
                if job['state'] not in ('queued','target_reached','waiting_form'):
                    raise ValueError('旧队列有已试填、暂停或结果不明的任务，请先核验，不能重新试填')
                self.validate_job_binding(job)
                if job['batch_status']!='active':raise ValueError('旧批次已暂停、结束或待核查，不能恢复')
                held=[dict(r) for r in self.db.execute("SELECT * FROM items WHERE batch_id=? AND status IN ('reserved','uncertain')",(job['batch_id'],))]
                if job['state']=='waiting_form':
                    item=job['item']
                    if (len(held)!=1 or not item or held[0]['id']!=job['item_id']
                            or item['status']!='reserved' or item['source']!='phone'
                            or item['account']!=job['account'] or item['contact_number'] is not None
                            or job['preview_number'] is not None):
                        raise ValueError('待填写占用记录已变化，请先核验，不重新领取')
                    if self.db.execute("SELECT 1 FROM events WHERE item_id=? AND action='contact_queue_fill_started'",(job['item_id'],)).fetchone():
                        raise ValueError('这位联系人曾开始试填，请先核验实际结果，不能恢复为未填写')
                    if self.db.execute('SELECT 1 FROM contact_queue_openings WHERE job_id=? AND item_id=?',(job['id'],job['item_id'])).fetchone():
                        raise ValueError('这位联系人曾尝试打开表单，请先核查实际页面，不能重新执行打开')
                    waiting.append(job)
                elif held or job['item_id'] is not None or job['preview_number'] is not None:
                    raise ValueError('旧队列存在未确认占用记录，停止核查')
            if len(waiting)!=1:raise ValueError('旧队列需要恰好一位尚未开始试填的已占用手机号')
            new_id=self.db.execute("INSERT INTO contact_queues(state,target_mode,reason,auto_submit,auto_close_profile) VALUES('configured','pinned',?,?,?)",(f'恢复队列{queue_id}的未填写任务；原历史保留',int(old['auto_submit']),int(old['auto_close_profile']))).lastrowid
            for job in old['jobs']:
                self.db.execute('''INSERT INTO contact_queue_jobs(queue_id,position,batch_id,window_json,
                    targets_json,binding_json,state,item_id,preview_number,reason) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (new_id,job['position'],job['batch_id'],json.dumps(job['window'],ensure_ascii=False),
                     json.dumps(job['target_groups'],ensure_ascii=False),json.dumps(job['pinned_binding'],ensure_ascii=False),
                     job['state'],job['item_id'],None,job['reason']))
            if old['state']!='cleared':
                self.db.execute("UPDATE contact_queues SET state='cleared',reason=reason||'；已恢复未填写任务到新队列' WHERE id=?",(queue_id,))
            self.store._event('contact_queue_restored',detail=json.dumps({'from_queue':queue_id,'to_queue':new_id,'held_item_id':waiting[0]['item_id'],'items_released':False,'numbers_reset':False},ensure_ascii=False))
            self.db.commit()
            return new_id
        except Exception:
            self.db.rollback()
            raise

    def configure(self, entries, *, auto_submit=False, auto_close_profile=False, new_batch=False, independent=False, start_phone_seq=1, contact_source='phone', start_username_seq=1):
        if type(auto_submit) is not bool:raise ValueError('自动提交设置必须明确为开启或关闭')
        if type(auto_close_profile) is not bool or (auto_close_profile and not auto_submit):raise ValueError('自动关闭资料页仅用于明确启用自动 Create 的队列')
        if type(new_batch) is not bool:raise ValueError('新批次选项无效')
        if type(start_phone_seq) is not int or start_phone_seq<1:
            raise ValueError('起始手机号名单序号必须为正整数')
        if start_phone_seq>1 and not (new_batch and independent):
            raise ValueError('指定起始序号仅用于独立手机号新批次')
        if contact_source not in ('phone','username') or type(start_username_seq) is not int or start_username_seq<1:
            raise ValueError('名单类型或用户名起始序号无效')
        if contact_source=='username' and not (independent and new_batch and auto_submit):
            raise ValueError('用户名自动添加仅支持独立新批次和自动提交')
        entries = list(entries)
        windows = addition_windows([e['window'] for e in entries])
        accounts = [str(e['account']).strip() for e in entries]
        if any(not a or len(a) > 100 for a in accounts) or len(set(accounts)) != len(accounts):
            raise ValueError('每个窗口必须绑定不同的稳定账号备注，长度 1–100 字')
        if any(type(e['target']) is not int or not 1 <= e['target'] <= 40 for e in entries):
            raise ValueError('每个账号的计划人数为 1–40')
        self.store._begin()
        try:
            if self.db.execute("SELECT 1 FROM contact_queues WHERE state='running'").fetchone():
                raise ValueError('请先停止正在执行的添加队列')
            self.db.execute("UPDATE contact_queues SET state='stopped',reason='已建立新队列' WHERE state='configured'")
            mode='contact' if independent else 'pinned' if self.pinned_groups is not None else 'catalog' if self.group_catalog is not None else 'none'
            queue_id = self.db.execute("INSERT INTO contact_queues(state,target_mode,auto_submit,auto_close_profile,start_phone_seq,contact_source,start_username_seq) VALUES('configured',?,?,?,?,?,?)",(mode,int(auto_submit),int(auto_close_profile),start_phone_seq,contact_source,start_username_seq)).lastrowid
            for position, (entry, account, window) in enumerate(zip(entries, accounts, windows), 1):
                binding=self.pinned_groups.queue_binding(account,window) if mode=='pinned' else None
                targets=[] if mode=='contact' else binding['targets'] if binding else [] if self.group_catalog is None else self.group_catalog.queue_targets(account,window)
                if new_batch:
                    if contact_source=='username':
                        held=self.db.execute("SELECT seq FROM items WHERE account=? AND source='username' AND status IN ('reserved','uncertain') ORDER BY seq LIMIT 1",(account,)).fetchone()
                        if held:raise ValueError(f'{account} 有未确认用户名（名单序号{held["seq"]}），请核查原任务；不能重复提交')
                    elif self.db.execute("SELECT 1 FROM items WHERE account=? AND status IN ('reserved','uncertain') AND (source!='phone' OR seq>=?)",(account,start_phone_seq)).fetchone():
                        raise ValueError(f'{account} 有未确认联系人，请先核查；旧记录不能交给新批次重试')
                states="'active','paused','review'" if new_batch else "'active','waiting','paused','review'"
                batch = self.db.execute(f"SELECT * FROM batches WHERE account=? AND status IN ({states}) ORDER BY id DESC", (account,)).fetchone()
                if batch and batch['mode']=='phone' and contact_source=='username' and new_batch:
                    if (batch['status']!='active' or
                            self.db.execute("SELECT 1 FROM items WHERE batch_id=? AND status IN ('reserved','uncertain')",(batch['id'],)).fetchone() or
                            not self.db.execute('SELECT 1 FROM items WHERE batch_id=? AND contact_number IS NOT NULL',(batch['id'],)).fetchone()):
                        self.db.execute("UPDATE batches SET status='archived' WHERE id=?",(batch['id'],))
                        self.store._event('batch_archived_for_username_queue',batch_id=batch['id'],detail=json.dumps(
                            {'previous_status':batch['status'],'previous_mode':batch['mode'],'items_changed':False,'reason':'独立用户名新批次不接管旧手机号任务'},ensure_ascii=False))
                        batch=None
                if start_phone_seq>1 and batch:
                    # Keep old contacts and unresolved results in their original batch.
                    self.db.execute("UPDATE batches SET status='archived' WHERE id=?",(batch['id'],))
                    self.store._event('batch_archived_for_start_sequence',batch_id=batch['id'],detail=json.dumps(
                        {'previous_status':batch['status'],'start_phone_seq':start_phone_seq,'items_changed':False},ensure_ascii=False))
                    batch=None
                if new_batch and batch and batch['status']=='active':
                    added=self.db.execute('SELECT COUNT(*) FROM items WHERE batch_id=? AND contact_number IS NOT NULL',(batch['id'],)).fetchone()[0]
                    if added:
                        self.db.execute("UPDATE batches SET status='waiting' WHERE id=?",(batch['id'],))
                        self.store._event('batch_adding_closed_for_new_batch',batch_id=batch['id'],detail='保留成功联系人和原邀请名单；未确认邀请；开始独立新添加批次')
                        batch=None
                if batch:
                    if batch['status'] != 'active':
                        raise ValueError(f'{account} 已暂停、待核查或待邀请；不能重新加入添加队列')
                    if self.db.execute("SELECT 1 FROM items WHERE batch_id=? AND status IN ('reserved','uncertain')", (batch['id'],)).fetchone():
                        raise ValueError(f'{account} 有未确认联系人，请先在使用记录核查，不能重新试填')
                    added = self.db.execute('SELECT COUNT(*) FROM items WHERE batch_id=? AND contact_number IS NOT NULL', (batch['id'],)).fetchone()[0]
                    if entry['target'] < added:
                        raise ValueError(f'{account} 的计划人数不能少于已确认人数 {added}')
                    batch_id = batch['id']
                    if self._frozen(batch_id):
                        raise ValueError(f'{account} 本批已生成两群名单；请先完成并结束旧批次，再建立新批次')
                    if mode in ('pinned','contact') and batch['mode']!=contact_source:raise ValueError(f'{account} 本批名单类型与当前队列不同，请建立新批次')
                    self.db.execute('UPDATE batches SET target=? WHERE id=?', (entry['target'], batch_id))
                else:
                    batch_id = self.db.execute('INSERT INTO batches(account,target,next_number,mode) VALUES(?,?,?,?)',
                        (account, entry['target'], self.store.next_contact_number(),contact_source)).lastrowid
                    self.store._event('batch_created', batch_id=batch_id, detail=account)
                self.db.execute('INSERT INTO contact_queue_jobs(queue_id,position,batch_id,window_json,targets_json,binding_json) VALUES(?,?,?,?,?,?)',
                    (queue_id, position, batch_id, json.dumps(window, ensure_ascii=False),json.dumps(targets,ensure_ascii=False),json.dumps(binding,ensure_ascii=False)))
            self.store._event('contact_queue_configured', detail=json.dumps({'queue_id':queue_id,'accounts':len(entries),'source':contact_source,'start_seq':start_username_seq if contact_source=='username' else start_phone_seq,'numbers_preallocated':False},ensure_ascii=False))
            self.db.commit()
            return queue_id
        except Exception:
            self.db.rollback()
            raise

    def snapshot(self, queue_id=None):
        queue_id = queue_id or self.latest()
        row = self.db.execute('SELECT * FROM contact_queues WHERE id=?', (queue_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['auto_submit']=bool(result['auto_submit'])
        result['auto_close_profile']=bool(result['auto_close_profile'])
        result.update(scope='contact_addition_queue', contact_submit_automated=result['auto_submit'], final_invite_clicked=False,
            contact_profile_close_automated=result['auto_close_profile'],
            contact_form_open_automated=True,
            phone_only=result['target_mode'] in ('pinned','contact') and result['contact_source']=='phone', next_number=self.store.next_contact_number(), jobs=[])
        for job in self.db.execute('SELECT * FROM contact_queue_jobs WHERE queue_id=? ORDER BY position', (queue_id,)):
            job = dict(job)
            job['window'] = json.loads(job.pop('window_json'))
            job['target_groups'] = json.loads(job.pop('targets_json'))
            job['pinned_binding'] = json.loads(job.pop('binding_json'))
            job['fill_report']=json.loads(job.pop('fill_json') or 'null')
            opened=self.db.execute('SELECT report_json FROM contact_queue_openings WHERE job_id=? AND item_id=?',(job['id'],job['item_id'])).fetchone()
            job['opening_report']=json.loads(opened[0]) if opened and opened[0] else None
            closing=self.db.execute('SELECT * FROM contact_queue_profile_closings WHERE job_id=? AND item_id=?',(job['id'],job['item_id'])).fetchone()
            job['profile_closing']=dict(closing) if closing else None
            if job['profile_closing']:job['profile_closing'].pop('reads_json');job['profile_closing'].pop('report_json')
            batch = self.store.batch(job['batch_id'])
            job.update(account=batch['account'], target=batch['target'], batch_status=batch['status'])
            job['added_numbers'] = [r[0] for r in self.db.execute('SELECT contact_number FROM items WHERE batch_id=? AND contact_number IS NOT NULL ORDER BY contact_number', (job['batch_id'],))]
            job['item'] = None
            if job['item_id']:
                item = self.db.execute('SELECT * FROM items WHERE id=?', (job['item_id'],)).fetchone()
                job['item'] = dict(item) if item else None
            result['jobs'].append(job)
        return result

    def _frozen(self,batch_id):
        for table in ('batch_group_plans','pinned_batch_member_plans'):
            if (self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
                    and self.db.execute(f'SELECT 1 FROM {table} WHERE batch_id=?',(batch_id,)).fetchone()):
                return True
        return False

    def validate_job_binding(self,job):
        source=self.db.execute('SELECT contact_source FROM contact_queues WHERE id=?',(job['queue_id'],)).fetchone()[0]
        if source=='username' and (job.get('item') and job['item']['source']!='username'):
            raise ValueError('用户名任务的已占用记录类型改变，停止核查')
        mode=self.db.execute('SELECT target_mode FROM contact_queues WHERE id=?',(job['queue_id'],)).fetchone()[0]
        if mode=='pinned':
            from pinned_members import same_binding
            if self.pinned_groups is None:raise ValueError('置顶群队列缺少账号绑定，停止核查')
            current=self.pinned_groups.queue_binding(job['account'],job['window'])
            if not job.get('pinned_binding') or not same_binding(job['pinned_binding'],current):
                raise ValueError(f'{job["account"]} 的两个置顶群、顺序或窗口绑定已变化，请重新核对队列')
        elif mode=='catalog':
            if self.group_catalog is None:raise ValueError('旧队列缺少完整群目录配置，请重新建立队列')
            current=self.group_catalog.queue_targets(job['account'],job['window'])
            if [(g['peer_key'],g['owner_id']) for g in job['target_groups']]!=[(g['peer_key'],g['owner_id']) for g in current]:
                raise ValueError('本队列没有两个群或群配置已改变；请重新建立并核对账号队列')
        if self._frozen(job['batch_id']):raise ValueError('本批两群名单已冻结，不能继续添加联系人')

    def current(self, queue_id=None):
        snapshot = self.snapshot(queue_id)
        if not snapshot or snapshot['state'] != 'running':
            return None
        return next((job for job in snapshot['jobs'] if job['state'] in ('waiting_form', 'closing_profile', 'opening', 'filling', 'filled', 'submitting', 'submitted','dismissing_unregistered')), None)

    def start(self, queue_id):
        snapshot=self.snapshot(queue_id)
        if not snapshot or snapshot['state']!='configured':raise ValueError('只能开始已配置的新队列')
        for job in snapshot['jobs']:
            self.validate_job_binding(job)
        with self.db:
            changed = self.db.execute("UPDATE contact_queues SET state='running',reason='' WHERE id=? AND state='configured'", (queue_id,)).rowcount
            if not changed:
                raise ValueError('只能开始已配置的新队列；中断的联系人必须先核查')
        return self.prepare(queue_id)

    def stop(self, queue_id, reason='用户停止队列', review=False):
        with self.db:
            self.db.execute('UPDATE contact_queues SET state=?,reason=? WHERE id=?', ('review' if review else 'stopped', reason, queue_id))
            self.store._event('contact_queue_stopped', detail=f'队列{queue_id}：{reason}')

    def prepare(self, queue_id):
        snapshot = self.snapshot(queue_id)
        if not snapshot or snapshot['state'] != 'running':
            return None
        for job in snapshot['jobs']:
            if job['state'] in ('paused', 'target_reached', 'no_list'):
                continue
            if job['batch_status'] != 'active':
                self.stop(queue_id, f'{job["account"]} 批次状态改变，需要核查', review=True)
                return None
            try:self.validate_job_binding(job)
            except ValueError as error:
                self.stop(queue_id,str(error),review=True)
                return None
            if job['state'] in ('closing_profile', 'opening', 'filling', 'filled', 'submitting', 'submitted','dismissing_unregistered'):
                return job
            if len(job['added_numbers']) >= job['target']:
                with self.db:
                    self.db.execute("UPDATE contact_queue_jobs SET state='target_reached',item_id=NULL,preview_number=NULL WHERE id=?", (job['id'],))
                continue
            item, is_new = self.store.reserve_next(job['batch_id'],phone_only=snapshot['phone_only'],start_phone_seq=snapshot['start_phone_seq'],username_only=snapshot['contact_source']=='username',start_username_seq=snapshot['start_username_seq'])
            if item is None:
                with self.db:
                    self.db.execute("UPDATE contact_queue_jobs SET state='no_list',reason=? WHERE id=?", ('没有可用手机号' if snapshot['phone_only'] else '没有可用用户名' if snapshot['contact_source']=='username' else '没有可用名单',job['id']))
                continue
            if item['status'] != 'reserved' or (not is_new and job['item_id'] != item['id']):
                self.stop(queue_id, '发现不属于当前准备步骤的未确认联系人；不自动重复添加', review=True)
                return None
            with self.db:
                self.db.execute("UPDATE contact_queue_jobs SET state='waiting_form',item_id=?,preview_number=NULL,fill_json=NULL WHERE id=?", (item['id'], job['id']))
            return self.current(queue_id)
        with self.db:
            self.db.execute("UPDATE contact_queues SET state='done',reason='添加队列已结束；各账号已添加联系人保留，群选人和最终邀请仍需另行操作' WHERE id=?", (queue_id,))
        return None

    def claim_profile_close(self,queue_id,job_id,reports):
        self.store._begin()
        try:
            job=self.current(queue_id)
            if (not job or job['id']!=job_id or job['state']!='waiting_form' or
                    not self.snapshot(queue_id)['auto_close_profile'] or job['item']['status']!='reserved'
                    or job['item']['source']!='phone' or job['preview_number'] is not None):
                raise ValueError('当前任务不允许自动关闭资料页，未点击')
            self.validate_job_binding(job)
            previous=self.db.execute('''SELECT * FROM items WHERE batch_id=? AND account=?
                AND status IN ('added','waiting','invited') AND numbering_global=1
                AND added_at IS NOT NULL AND contact_number IS NOT NULL ORDER BY contact_number DESC LIMIT 1''',
                (job['batch_id'],job['account'])).fetchone()
            if not previous or previous['contact_number']>=self.store.next_contact_number() or not previous_profile_verified(reports,job['window'],dict(previous)):
                raise ValueError('当前资料页没有两次完整匹配本账号上一位成功联系人，未关闭')
            if self.db.execute('SELECT 1 FROM contact_queue_profile_closings WHERE job_id=? AND item_id=?',(job_id,job['item_id'])).fetchone():
                raise ValueError('该任务已经尝试关闭资料页，不自动重试')
            self.db.execute('''INSERT INTO contact_queue_profile_closings(queue_id,job_id,item_id,previous_item_id,
                previous_number,previous_phone,profile_runtime_id,reads_json) VALUES(?,?,?,?,?,?,?,?)''',
                (queue_id,job_id,job['item_id'],previous['id'],previous['contact_number'],previous['value'],reports[0]['scope_runtime_id'],json.dumps(reports,ensure_ascii=False)))
            self.db.execute("UPDATE contact_queue_jobs SET state='closing_profile' WHERE id=?",(job_id,))
            self.store._event('contact_queue_profile_close_started',job['item_id'],job['batch_id'],f'队列{queue_id}；关闭已核验联系人{previous["contact_number"]}的资料页，未提交下一位')
            self.db.commit();return self.current(queue_id)
        except Exception:
            self.db.rollback();raise

    def save_profile_close_result(self,queue_id,job_id,item_id,report,error=None):
        with self.db:
            changed=self.db.execute('''UPDATE contact_queue_profile_closings SET report_json=?,error=?
                WHERE queue_id=? AND job_id=? AND item_id=? AND report_json IS NULL''',
                (json.dumps(report,ensure_ascii=False),error,queue_id,job_id,item_id)).rowcount
            if changed!=1:raise ValueError('关闭资料页报告没有对应的未完成操作，未推进任务')

    def finish_profile_close(self,queue_id,job_id,item_id):
        self.store._begin()
        try:
            job=self.current(queue_id)
            if not job or job['id']!=job_id or job['item_id']!=item_id or job['state']!='closing_profile':
                raise ValueError('关闭资料页期间任务已停止或改变，不继续添加')
            self.validate_job_binding(job)
            row=self.db.execute('SELECT * FROM contact_queue_profile_closings WHERE job_id=? AND item_id=?',(job_id,item_id)).fetchone()
            if not row or row['report_json'] is None or row['error'] or not profile_close_verified(json.loads(row['report_json']),job['window'],dict(row)):
                raise ValueError('关闭结果未完整核对，停止，不重试或继续添加')
            previous=self.db.execute('SELECT * FROM items WHERE id=?',(row['previous_item_id'],)).fetchone()
            if (not previous or previous['batch_id']!=job['batch_id'] or previous['account']!=job['account']
                    or previous['contact_number']!=row['previous_number'] or previous['value']!=row['previous_phone']
                    or previous['status'] not in ('added','waiting','invited')):
                raise ValueError('上一位成功记录变化，不继续添加')
            self.db.execute("UPDATE contact_queue_jobs SET state='waiting_form' WHERE id=?",(job_id,))
            self.db.commit()
        except Exception:
            self.db.rollback();raise

    def claim_open(self, queue_id, job_id):
        """Commit one opening attempt before sending a native command. Never replay."""
        self.store._begin()
        try:
            job=self.current(queue_id)
            if (not job or job['id']!=job_id or job['state']!='waiting_form'
                    or not job['item'] or job['item']['status']!='reserved'
                    or job['item']['source'] not in ('phone','username') or job['preview_number'] is not None):
                raise ValueError('队列任务已改变，未打开联系人表单')
            self.validate_job_binding(job)
            paths=[path_key(j['window']) for j in self.snapshot(queue_id)['jobs']]
            if len(set(paths))!=len(paths):raise ValueError('同一程序路径绑定了多个窗口，无法确定表单所在账号')
            if self.db.execute('SELECT 1 FROM contact_queue_openings WHERE job_id=? AND item_id=?',
                    (job_id,job['item_id'])).fetchone():
                raise ValueError('这位联系人已尝试打开表单；核查实际页面，不自动重试')
            self.db.execute('INSERT INTO contact_queue_openings(queue_id,job_id,item_id) VALUES(?,?,?)',
                (queue_id,job_id,job['item_id']))
            self.db.execute("UPDATE contact_queue_jobs SET state='opening' WHERE id=?",(job_id,))
            self.store._event('contact_queue_open_started',job['item_id'],job['batch_id'],f'队列{queue_id}；仅打开空白表单，尚未填写或提交')
            self.db.commit()
            return self.current(queue_id)
        except Exception:
            self.db.rollback();raise

    def save_open_result(self, queue_id, job_id, item_id, report, error=None):
        """Keep late results after Stop for audit; never change jobs or numbering."""
        with self.db:
            changed=self.db.execute('''UPDATE contact_queue_openings SET report_json=?,error=?
                WHERE queue_id=? AND job_id=? AND item_id=? AND report_json IS NULL''',
                (json.dumps(report,ensure_ascii=False),error,queue_id,job_id,item_id)).rowcount
            if changed!=1:raise ValueError('打开表单报告没有对应的未完成操作，未更新任务')

    def finish_open(self, queue_id, job_id, item_id):
        self.store._begin()
        try:
            job=self.current(queue_id)
            if not job or job['id']!=job_id or job['item_id']!=item_id or job['state']!='opening':
                raise ValueError('打开期间队列已停止或任务改变，不继续填写')
            self.validate_job_binding(job)
            row=self.db.execute('SELECT report_json,error FROM contact_queue_openings WHERE queue_id=? AND job_id=? AND item_id=?',
                (queue_id,job_id,item_id)).fetchone()
            from username_contact import opening_verified
            verified=(opening_verified(json.loads(row['report_json']),job) if job['item']['source']=='username' else form_open_verified(json.loads(row['report_json']),job['window'])) if row and row['report_json'] else False
            if not row or row['report_json'] is None or row['error'] or not verified:
                raise ValueError('未完整核对原账号空白表单，停止，不自动重试')
            self.db.execute("UPDATE contact_queue_jobs SET state='waiting_form' WHERE id=?",(job_id,))
            self.db.commit()
        except Exception:
            self.db.rollback();raise

    def claim_fill(self, queue_id, job_id):
        self.store._begin()
        try:
            job = self.current(queue_id)
            if not job or job['id'] != job_id or job['state'] != 'waiting_form' or job['item']['status'] != 'reserved':
                raise ValueError('队列任务已改变，未试填')
            if job['item']['source']=='username':
                from username_contact import opening_verified
                if not opening_verified(job.get('opening_report') or {},job):
                    raise ValueError('用户名表单尚未由本任务核验打开，不能填写')
            self.validate_job_binding(job)
            number = self.store.next_contact_number()
            if number > 999999:
                raise ValueError('编号已用完，未试填')
            self.db.execute("UPDATE contact_queue_jobs SET state='filling',preview_number=? WHERE id=?", (number, job_id))
            self.store._event('contact_queue_fill_started', job['item_id'], job['batch_id'], f'队列{queue_id}；预览编号{number}；尚未确认成功')
            self.db.commit()
            return self.current(queue_id)
        except Exception:
            self.db.rollback()
            raise

    def finish_fill(self, queue_id, job_id, report):
        job = self.current(queue_id)
        if not job or job['id'] != job_id or job['state'] != 'filling':
            raise ValueError('试填期间队列已停止；核查实际表单，不自动重试')
        self.validate_job_binding(job)
        from username_contact import fill_verified
        phone_verified=(report.get('ok') is True and report.get('mode') == 'fill_only'
                and report.get('number') == str(job['preview_number']) and report.get('phone_matches') is True
                and report.get('empty_guard') is True
                and report.get('contact_created') is False and report.get('final_invite_clicked') is False)
        verified=fill_verified(report,job) if job['item']['source']=='username' else phone_verified
        if not verified:
            self.stop(queue_id, '试填未完整核对；检查实际表单，不自动重试', review=True)
            raise ValueError('试填未完整核对，队列停止')
        with self.db:
            self.db.execute("UPDATE contact_queue_jobs SET state='filled',fill_json=? WHERE id=?", (json.dumps(report,ensure_ascii=False),job_id))

    def claim_submit(self, queue_id, job_id):
        """Persist the irreversible attempt before native Create; never allocate here."""
        self.store._begin()
        try:
            job=self.current(queue_id)
            if (not job or job['id']!=job_id or job['state']!='filled' or
                    not self.snapshot(queue_id)['auto_submit'] or job['item']['status']!='reserved'):
                raise ValueError('当前任务未启用自动提交或状态已改变，未点击 Create')
            self.validate_job_binding(job)
            if self.store.next_contact_number()!=job['preview_number']:raise ValueError('全局编号已改变，未点击 Create')
            submission_payload(job)
            if self.db.execute('SELECT 1 FROM contact_queue_submissions WHERE job_id=? AND item_id=?',(job_id,job['item_id'])).fetchone():
                raise ValueError('该联系人曾尝试提交，必须核查实际结果，不自动重试')
            self.db.execute('INSERT INTO contact_queue_submissions(queue_id,job_id,item_id,number,fill_json) VALUES(?,?,?,?,?)',
                (queue_id,job_id,job['item_id'],job['preview_number'],json.dumps(job['fill_report'],ensure_ascii=False)))
            self.db.execute("UPDATE contact_queue_jobs SET state='submitting' WHERE id=?",(job_id,))
            self.store._event('contact_queue_submit_started',job['item_id'],job['batch_id'],f'队列{queue_id}；准备唯一一次 Create，编号尚未登记')
            self.db.commit();return self.current(queue_id)
        except Exception:
            self.db.rollback();raise

    def save_submit_result(self,queue_id,job_id,item_id,report,error=None):
        with self.db:
            changed=self.db.execute('''UPDATE contact_queue_submissions SET report_json=?,error=?
                WHERE queue_id=? AND job_id=? AND item_id=? AND report_json IS NULL''',
                (json.dumps(report,ensure_ascii=False),error,queue_id,job_id,item_id)).rowcount
            if changed!=1:raise ValueError('提交报告没有对应的未完成操作，不推进任务')

    def finish_submit(self,queue_id,job_id,item_id):
        self.store._begin()
        try:
            job=self.current(queue_id)
            if not job or job['id']!=job_id or job['item_id']!=item_id or job['state']!='submitting':
                raise ValueError('提交期间任务已停止或改变，不登记成功')
            self.validate_job_binding(job)
            if self.store.next_contact_number()!=job['preview_number']:raise ValueError('提交期间全局编号改变，请核查实际联系人')
            row=self.db.execute('SELECT report_json,error FROM contact_queue_submissions WHERE queue_id=? AND job_id=? AND item_id=?',
                (queue_id,job_id,item_id)).fetchone()
            if not row or row['report_json'] is None or row['error'] or not submission_verified(json.loads(row['report_json']),job):
                raise ValueError('提交结果没有完整核对；Create 可能已执行，停止核查，不重试')
            self.db.execute("UPDATE contact_queue_jobs SET state='submitted' WHERE id=?",(job_id,))
            self.db.commit()
        except Exception:
            self.db.rollback();raise

    def recover_submitted_profile(self,queue_id,job_id,reports):
        self.store._begin()
        try:
            snap=self.snapshot(queue_id)
            if not snap or snap['state'] not in ('review','stopped'):raise ValueError('只恢复已停止并待核查的提交')
            jobs=[j for j in snap['jobs'] if j['id']==job_id]
            if len(jobs)!=1:raise ValueError('原任务不唯一')
            job=jobs[0]
            if (job['state'] not in ('submitting','submitted') or not job['item']
                    or job['item']['source']!='phone' or job['item']['status']!='reserved'
                    or job['item']['contact_number'] is not None or job['batch_status']!='active'
                    or self.store.next_contact_number()!=job['preview_number']):
                raise ValueError('原任务、占用记录或全局编号已变化，不恢复')
            self.validate_job_binding(job)
            row=self.db.execute('SELECT report_json FROM contact_queue_submissions WHERE queue_id=? AND job_id=? AND item_id=?',
                (queue_id,job_id,job['item_id'])).fetchone()
            action=json.loads(row[0]) if row and row[0] else {};fill=job['fill_report'] or {};w=job['window']
            if not (action.get('create_invoked') is True and action.get('fields_verified') is True
                    and action.get('process_path_verified') is True and action.get('number')==str(job['preview_number'])
                    and action.get('window_handle')==w['hwnd'] and action.get('process_id')==w['pid']
                    and action.get('main_runtime_id')==fill.get('main_runtime_id')
                    and action.get('contact_runtime_id')==fill.get('contact_runtime_id')):
                raise ValueError('缺少原任务 Create 提交证据，不恢复')
            outcome=confirmed_observation(reports,w,job['item'],job['preview_number'])
            if not outcome or outcome['outcome']!='added':raise ValueError('两次原手机号资料页核验未通过，不登记')
            self.db.execute('INSERT INTO contact_queue_profile_recoveries VALUES(?,?,?,?,?)',
                (queue_id,job_id,job['item_id'],job['preview_number'],json.dumps(reports,ensure_ascii=False)))
            number=job['preview_number']
            self.db.execute('UPDATE number_sequence SET next_value=next_value+1 WHERE id=1')
            self.db.execute('UPDATE batches SET next_number=?',(number+1,))
            self.db.execute("UPDATE items SET status='added',contact_number=?,numbering_global=1,added_at=strftime('%Y-%m-%d %H:%M:%f','now'),updated=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(number,job['item_id']))
            self.db.execute("UPDATE contact_queue_jobs SET state='waiting_form',item_id=NULL,preview_number=NULL,lookup_failures=0 WHERE id=?",(job_id,))
            self.db.execute("UPDATE contact_queues SET state='running',reason='' WHERE id=?",(queue_id,))
            self.store._event('added',job['item_id'],job['batch_id'],'提交后中断，通过两次原手机号资料页核验恢复；未再次 Create')
            self.store._event('contact_queue_observation',job['item_id'],job['batch_id'],json.dumps({'queue':queue_id,'outcome':outcome,'reports':reports},ensure_ascii=False))
            self.db.commit()
        except Exception:self.db.rollback();raise
        self.prepare(queue_id)

    def claim_unregistered(self,queue_id,job_id,reports,*,recovery=False):
        self.store._begin()
        try:
            snap=self.snapshot(queue_id)
            if not snap or snap['state'] not in (('review','stopped') if recovery else ('running',)):
                raise ValueError('未注册核验已过期，队列状态不匹配')
            jobs=[j for j in snap['jobs'] if j['id']==job_id]
            if len(jobs)!=1:raise ValueError('未找到原占用任务')
            job=jobs[0]
            if (job['state'] not in ('submitting','submitted') or not job['item']
                    or job['item']['source']!='phone' or job['item']['status']!='reserved'
                    or job['item']['contact_number'] is not None or job['batch_status']!='active'
                    or self.store.next_contact_number()!=job['preview_number']):
                raise ValueError('原手机号任务或编号已改变，不自动跳过')
            self.validate_job_binding(job)
            proof=unregistered_evidence(reports,job['window'])
            if not proof:raise ValueError('两次未注册结果结构核验未通过，不跳过')
            action=self.db.execute('SELECT report_json FROM contact_queue_submissions WHERE queue_id=? AND job_id=? AND item_id=?',
                (queue_id,job_id,job['item_id'])).fetchone()
            submitted=json.loads(action[0]) if action and action[0] else {}
            fill=job['fill_report'] or {};w=job['window']
            if not (submitted.get('create_invoked') is True and submitted.get('fields_verified') is True
                    and submitted.get('process_path_verified') is True
                    and submitted.get('window_handle')==w['hwnd'] and submitted.get('process_id')==w['pid']
                    and submitted.get('number')==str(job['preview_number'])
                    and submitted.get('main_runtime_id')==fill.get('main_runtime_id')
                    and submitted.get('contact_runtime_id')==fill.get('contact_runtime_id')):
                raise ValueError('缺少原手机号唯一 Create 提交记录，不能跳过')
            if not recovery and submitted.get('result_runtime_id')!=proof['runtime_id']:
                raise ValueError('未注册结果不是原提交弹窗')
            self.db.execute('INSERT INTO contact_queue_unregistered(queue_id,job_id,item_id,number,dialog_id,reads_json,recovery) VALUES(?,?,?,?,?,?,?)',
                (queue_id,job_id,job['item_id'],job['preview_number'],proof['runtime_id'],json.dumps(reports,ensure_ascii=False),int(recovery)))
            self.db.execute("UPDATE contact_queues SET state='running',reason='' WHERE id=?",(queue_id,))
            self.db.execute("UPDATE contact_queue_jobs SET state='dismissing_unregistered' WHERE id=?",(job_id,))
            self.store._event('unregistered_dismiss_started',job['item_id'],job['batch_id'],
                '原号码人工重现核验' if recovery else '原提交结果核验')
            self.db.commit()
            return job,{'executable_path':w['path'],'main_runtime_id':fill['main_runtime_id'],
                'dialog_runtime_id':proof['runtime_id'],'number':str(job['preview_number'])}
        except Exception:self.db.rollback();raise

    def save_unregistered_result(self,queue_id,job_id,item_id,report,error=None):
        with self.db:
            changed=self.db.execute('UPDATE contact_queue_unregistered SET report_json=?,error=? WHERE queue_id=? AND job_id=? AND item_id=? AND report_json IS NULL',
                (json.dumps(report,ensure_ascii=False),error,queue_id,job_id,item_id)).rowcount
            if changed!=1:raise ValueError('没有对应未注册关闭任务，不推进')

    def finish_unregistered(self,queue_id,job_id,item_id):
        self.store._begin()
        try:
            job=self.current(queue_id)
            if not job or job['id']!=job_id or job['item_id']!=item_id or job['state']!='dismissing_unregistered':
                raise ValueError('关闭期间队列已停止或任务改变，不推进')
            self.validate_job_binding(job)
            row=self.db.execute('SELECT * FROM contact_queue_unregistered WHERE queue_id=? AND job_id=? AND item_id=?',(queue_id,job_id,item_id)).fetchone()
            from unregistered_contact import dismissal_verified
            r=json.loads(row['report_json']) if row and row['report_json'] else {}
            if not row or row['error'] or not dismissal_verified(r,job,row['dialog_id']):
                raise ValueError('未注册结果关闭未核对，不跳过或自动重试')
            if self.store.next_contact_number()!=job['preview_number'] or job['item']['status']!='reserved':
                raise ValueError('编号或占用记录变化，不跳过')
            reason='手机号添加失败，待核查；弹窗不能证明未注册，保留未使用编号'
            self.db.execute("UPDATE items SET status='lookup_failed',reason=?,updated=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(reason,item_id))
            self.db.execute("UPDATE contact_queue_jobs SET state='waiting_form',item_id=NULL,preview_number=NULL,fill_json=NULL,lookup_failures=lookup_failures+1 WHERE id=?",(job_id,))
            self.store._event('phone_lookup_failed',item_id,job['batch_id'],reason)
            count=self.db.execute('SELECT lookup_failures FROM contact_queue_jobs WHERE id=?',(job_id,)).fetchone()[0]
            if count>=2:
                warning=f'{job["account"]}连续{count}次出现手机号添加失败提示，已跳过本账号，继续下一个账号；失败号码保留待核查。'
                self.db.execute("UPDATE contact_queue_jobs SET state='paused',reason=? WHERE id=?",(warning,job_id))
                self.store._event('contact_queue_account_skipped',item_id,job['batch_id'],warning)
            self.db.commit()
        except Exception:self.db.rollback();raise
        self.prepare(queue_id)

    def accept(self, queue_id, job_id, reports):
        """Persist restriction and advance atomically; never release an uncertain user."""
        self.store._begin()
        try:
            job = self.current(queue_id)
            if not job or job['id'] != job_id or job['state'] not in ('filled','submitted') or job['item']['status'] != 'reserved':
                raise ValueError('观察结果已过期，不更新队列')
            if job['batch_status'] != 'active':
                raise ValueError('批次状态已改变，不更新队列')
            self.validate_job_binding(job)
            if job['state']=='filled' and self.snapshot(queue_id)['auto_submit']:
                raise ValueError('自动提交任务还未完成提交结果核对，不提前登记')
            if job['state']=='submitted':
                action=self.db.execute('SELECT report_json FROM contact_queue_submissions WHERE job_id=? AND item_id=?',(job_id,job['item_id'])).fetchone()
                submitted=json.loads(action['report_json']) if action and action['report_json'] else {}
                expected_id=submitted.get('profile_runtime_id') if submitted.get('state')=='profile_opened' else submitted.get('result_runtime_id')
                if not expected_id or any(r.get('scope_runtime_id')!=expected_id for r in reports):
                    raise ValueError('提交后的资料页或结果弹窗身份发生变化，停止核查')
            if job['item']['account']!=job['account'] or job['item']['contact_number'] is not None:
                raise ValueError('当前占用记录与账号或编号不一致，停止核查')
            outcome = confirmed_observation(reports, job['window'], job['item'], job['preview_number'], single_profile=job['state']=='submitted')
            if not outcome:
                self.db.rollback()
                return None
            if self.store.next_contact_number() != job['preview_number']:
                raise ValueError('全局编号已改变，请核查已填写的联系人，不自动登记')
            if outcome['outcome'] == 'restriction':
                self.db.execute("UPDATE batches SET status='paused',reason=? WHERE id=?", (outcome['reason'], job['batch_id']))
                self.db.execute("UPDATE items SET status='uncertain',reason=?,updated=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?", (outcome['reason'], job['item_id']))
                self.db.execute("UPDATE contact_queue_jobs SET state='paused',reason=? WHERE id=?", (outcome['reason'], job_id))
                self.store._event('restriction', job['item_id'], job['batch_id'], outcome['reason'])
                self.db.execute("UPDATE contact_queues SET state='review',reason=? WHERE id=?",(f'{job["account"]}已识别添加限制，请检查；整个添加队列已暂停。',queue_id))
            else:
                number = job['preview_number']
                self.db.execute('UPDATE number_sequence SET next_value=next_value+1 WHERE id=1')
                self.db.execute('UPDATE batches SET next_number=?', (number + 1,))
                self.db.execute("UPDATE items SET status='added',contact_number=?,numbering_global=1,added_at=strftime('%Y-%m-%d %H:%M:%f','now'),updated=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?", (number, job['item_id']))
                self.db.execute("UPDATE contact_queue_jobs SET state='waiting_form',item_id=NULL,preview_number=NULL,lookup_failures=0 WHERE id=?", (job_id,))
                self.store._event('added', job['item_id'], job['batch_id'], outcome['reason'])
            self.store._event('contact_queue_observation', job['item_id'], job['batch_id'], json.dumps({'queue': queue_id, 'outcome': outcome, 'reports': reports}, ensure_ascii=False))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        # The previous result is durable before reserving or touching the next window.
        self.prepare(queue_id)
        return outcome

    def save_read(self, queue_id, job_id, reports):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO contact_queue_reads(queue_id,job_id,captured,reports_json) VALUES(?,?,strftime('%Y-%m-%d %H:%M:%f','now'),?)",
                (queue_id, job_id, json.dumps(reports, ensure_ascii=False)))

    def export(self, path, *, queue_id=None):
        path = Path(path)
        snapshot = self.snapshot(queue_id or self.latest() or self.latest(include_cleared=True))
        if not snapshot:
            raise ValueError('还没有配置添加队列')
        snapshot['profile_recoveries']=[{**dict(r),'reads':json.loads(r['reads_json'])} for r in
            self.db.execute('SELECT * FROM contact_queue_profile_recoveries WHERE queue_id=?',(snapshot['id'],))]
        for r in snapshot['profile_recoveries']:r.pop('reads_json')
        snapshot['unregistered_results']=[{**dict(r),'reads':json.loads(r['reads_json']),'report':json.loads(r['report_json']) if r['report_json'] else None}
            for r in self.db.execute('SELECT * FROM contact_queue_unregistered WHERE queue_id=?',(snapshot['id'],))]
        for r in snapshot['unregistered_results']:r.pop('reads_json');r.pop('report_json')
        snapshot['observations'] = []
        snapshot['profile_closings']=[{**dict(row),'reads':json.loads(row['reads_json']),'report':json.loads(row['report_json']) if row['report_json'] is not None else None}
            for row in self.db.execute('SELECT * FROM contact_queue_profile_closings WHERE queue_id=? ORDER BY started,job_id,item_id',(snapshot['id'],))]
        for action in snapshot['profile_closings']:action.pop('reads_json');action.pop('report_json')
        snapshot['form_submissions']=[{**dict(row),'fill':json.loads(row['fill_json']),'report':json.loads(row['report_json']) if row['report_json'] is not None else None}
            for row in self.db.execute('SELECT * FROM contact_queue_submissions WHERE queue_id=? ORDER BY started,job_id,item_id',(snapshot['id'],))]
        for action in snapshot['form_submissions']:action.pop('report_json');action.pop('fill_json')
        snapshot['form_openings']=[{**dict(row),'report':json.loads(row['report_json']) if row['report_json'] is not None else None}
            for row in self.db.execute('SELECT queue_id,job_id,item_id,started,error,report_json FROM contact_queue_openings WHERE queue_id=? ORDER BY started,job_id,item_id',(snapshot['id'],))]
        for action in snapshot['form_openings']:action.pop('report_json')
        last_read = self.db.execute('SELECT * FROM contact_queue_reads WHERE queue_id=?', (snapshot['id'],)).fetchone()
        snapshot['last_read'] = None if not last_read else {
            'job_id':last_read['job_id'], 'captured':last_read['captured'], 'reports':json.loads(last_read['reports_json'])}
        for row in self.db.execute("SELECT time,detail FROM events WHERE action='contact_queue_observation' ORDER BY id"):
            event = json.loads(row['detail'])
            if event['queue'] == snapshot['id']:
                snapshot['observations'].append({'time': row['time'], **event})
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
