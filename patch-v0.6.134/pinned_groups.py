"""Read-only pinned group observations. UI runtime IDs are never Telegram IDs.

These observations may guide navigation after fresh live guards and bind
the contact queue. UI runtime IDs never become stable Telegram group IDs.
"""
import copy
import json
import re

from groups import valid_window


LANGUAGES = {
    'Group': ('en', 'Pinned', 'Muted', 'Name'),
    'Grupo': ('pt', 'Fixado', 'Silenciado', 'Nome'),
}


def pinned_pair(report, window=None, *, require_fields=False):
    if (not isinstance(report, dict) or report.get('ok') is not True
            or report.get('read_only') is not True or report.get('scope') != 'group_catalog_probe'
            or report.get('truncated') is not False or report.get('errors') != []
            or report.get('final_invite_clicked') is not False
            or report.get('list_class') != 'class Dialogs::InnerWidget'
            or not report.get('list_runtime_id')):
        raise ValueError('群列表读取不完整或不在支持的聊天列表中，未记录配置')
    if window is not None and (not valid_window(window)
            or report.get('window_handle') != window['hwnd']
            or report.get('process_id') != window['pid']):
        raise ValueError('报告不属于当前选中的窗口，未记录配置')
    candidates = report.get('candidates')
    if not isinstance(candidates, list):raise ValueError('缺少聊天列表条目')
    rows = [row for row in candidates if isinstance(row, dict) and row.get('type') == 'ListItem']
    if len(rows) < 2:raise ValueError('聊天列表不足两个条目')
    targets = []
    for slot, row in enumerate(rows[:2], 1):
        label = row.get('name', '')
        if not isinstance(label, str):raise ValueError('聊天条目名称无效')
        kind = label.split(', ', 1)[0]
        if kind not in LANGUAGES:raise ValueError(f'第 {slot} 个聊天未读到支持的群类型；请只置顶两个目标群')
        language, pin, muted, name_field = LANGUAGES[kind]
        # Only a pin in the metadata prefix counts; never search message previews.
        # Comma-separated titles are ambiguous and intentionally require a later
        # structured-name adapter instead of silently choosing a different title.
        pattern = (re.escape(kind) + r', (?P<name>[^,\r\n]+), (?:(?:'
                   + re.escape(muted) + r'), )?' + re.escape(pin) + r'(?:, [\s\S]*)?')
        match = re.fullmatch(pattern, label)
        if not match or not match['name'].strip() or row.get('label_truncated') is not False:
            raise ValueError(f'第 {slot} 个聊天无法明确读出置顶群名称，未记录配置')
        runtime_id = row.get('runtime_id')
        if not isinstance(runtime_id, str) or not runtime_id:raise ValueError('缺少群条目控件标识')
        fields = row.get('child_field_names', [])
        if not isinstance(fields, list):raise ValueError('群条目字段结构无效')
        if require_fields and (pin not in fields or not any(x in fields for x in ('Name', name_field))):
            raise ValueError(f'第 {slot} 个群缺少独立的名称／置顶字段，未记录配置')
        targets.append({'slot':slot, 'name':match['name'], 'language':language,
            'group_label':kind, 'pinned_label':pin, 'runtime_id':runtime_id,
            'stable_group_id':None, 'name_review_required':True})
    if targets[0]['runtime_id'] == targets[1]['runtime_id']:
        raise ValueError('两个目标指向同一个控件，未记录配置')
    return {'scope':'pinned_group_observation', 'read_only':True,
        'list_runtime_id':report['list_runtime_id'], 'targets':targets,
        'observed_chat_rows':len(rows), 'all_groups_scanned':False,
        'stable_group_ids_available':False, 'account_identity_verified':False,
        'use_for_invitation':False, 'final_invite_clicked':False}


def consistent_pair(first, second, window):
    a = pinned_pair(first, window, require_fields=True)
    b = pinned_pair(second, window, require_fields=True)
    if a['list_runtime_id'] != b['list_runtime_id'] or a['targets'] != b['targets']:
        raise ValueError('两次读取的置顶群或顺序发生变化，未记录配置')
    return b


class PinnedGroupBindings:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS pinned_group_observations (
            account TEXT PRIMARY KEY, window_json TEXT NOT NULL, observation_json TEXT NOT NULL,
            saved TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')))''')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS pinned_group_navigation_runs (
                id INTEGER PRIMARY KEY, report_json TEXT NOT NULL,
                saved TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')));
            CREATE TABLE IF NOT EXISTS pinned_group_navigation_states (
                account TEXT NOT NULL, slot INTEGER NOT NULL CHECK(slot IN (1,2)),
                job_json TEXT NOT NULL, run_id INTEGER NOT NULL REFERENCES pinned_group_navigation_runs(id),
                PRIMARY KEY(account,slot));
        ''')
        self.db.commit()

    def accounts(self):
        return [r[0] for r in self.db.execute('SELECT account FROM pinned_group_observations ORDER BY account')]

    def get(self, account):
        row = self.db.execute('SELECT * FROM pinned_group_observations WHERE account=?', (account,)).fetchone()
        if not row:return None
        result = json.loads(row['observation_json'])
        result.update(account=account, window=json.loads(row['window_json']), saved=row['saved'])
        return result

    def queue_binding(self, account, window):
        from group_navigation import navigation_entries
        record=self.get(account)
        if not record:raise ValueError(f'{account} 尚未识别两个置顶群，请先识别并记录')
        if any(record['window'].get(k)!=window.get(k) for k in ('hwnd','pid','path')):
            raise ValueError(f'{account} 的置顶群窗口绑定不匹配，请核对所选窗口')
        return navigation_entries([record])[0]

    def save_navigation(self, result):
        if not isinstance(result,dict) or result.get('scope')!='pinned_group_navigation_queue':
            raise ValueError('不是群打开报告')
        self.store._begin()
        try:
            run_id=self.db.execute('INSERT INTO pinned_group_navigation_runs(report_json) VALUES(?)',
                (json.dumps(result,ensure_ascii=False),)).lastrowid
            for job in result['jobs']:
                if job['state'] not in ('members_open','review'):continue
                self.db.execute('''INSERT INTO pinned_group_navigation_states(account,slot,job_json,run_id)
                    VALUES(?,?,?,?) ON CONFLICT(account,slot) DO UPDATE SET job_json=excluded.job_json,run_id=excluded.run_id''',
                    (job['account'],job['slot'],json.dumps(job,ensure_ascii=False),run_id))
            self.db.commit()
        except Exception:self.db.rollback();raise
        return run_id

    def navigation_states(self, account):
        return {r['slot']:json.loads(r['job_json']) for r in self.db.execute(
            'SELECT slot,job_json FROM pinned_group_navigation_states WHERE account=?',(account,))}

    def latest_navigation(self):
        row=self.db.execute('SELECT report_json FROM pinned_group_navigation_runs ORDER BY id DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def save(self, account, window, first, second):
        account = account.strip()
        if not account or len(account)>100:raise ValueError('请填写稳定的账号备注，长度 1–100 字')
        observation = consistent_pair(first, second, window)
        observation.update(account=account,window=copy.deepcopy(window))
        return self.save_many([observation])[0]

    def _protect_unfinished_binding(self, record):
        from group_navigation import same_observed_targets
        previous=self.get(record['account'])
        if not previous:return
        unchanged=(all(previous['window'].get(k)==record['window'].get(k) for k in ('hwnd','pid','path'))
            and same_observed_targets(previous,record))
        if unchanged:return
        unfinished=False
        batches=self.db.execute("SELECT id,status FROM batches WHERE account=? AND status IN ('active','waiting','paused','review')",
            (record['account'],)).fetchall()
        has_queues=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contact_queue_jobs'").fetchone()
        for batch in batches:
            from selection_scope import batch_allowed
            if not batch_allowed(self.db,batch['id']):continue
            # Completed contact-only queues did not freeze invitation targets.
            # They must be allowed to establish a fresh binding before planning.
            contact_only=False
            if has_queues and batch['status']=='active':
                job=self.db.execute("SELECT j.targets_json,j.state,q.target_mode,q.state AS queue_state FROM contact_queue_jobs j JOIN contact_queues q ON q.id=j.queue_id WHERE j.batch_id=? ORDER BY j.id DESC LIMIT 1",(batch['id'],)).fetchone()
                held=self.db.execute("SELECT 1 FROM items WHERE batch_id=? AND status IN ('reserved','uncertain')",(batch['id'],)).fetchone()
                contact_only=bool(job and job['target_mode']=='contact' and job['queue_state']=='done'
                    and job['state'] in ('target_reached','no_list','paused')
                    and json.loads(job['targets_json'])==[] and not held)
            if not contact_only:unfinished=True
        table=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pinned_member_plans'").fetchone()
        if table:
            row=self.db.execute('SELECT plan_json FROM pinned_member_plans WHERE account=?',(record['account'],)).fetchone()
            if row:
                plan=json.loads(row[0])
                states=[json.loads(r[0]) for r in self.db.execute('SELECT job_json FROM pinned_member_states WHERE account=?',
                    (record['account'],))]
                completed={r.get('slot') for r in states if r.get('plan_id')==plan.get('plan_id') and r.get('state') in ('selection_finished','waiting_for_manual_invite','confirmed_invited')}
                if completed!={1,2}:
                    if plan.get('source')!='confirmed_batch':unfinished=True
                    else:
                        batch=self.db.execute('SELECT status FROM batches WHERE id=?',(plan.get('batch_id'),)).fetchone()
                        if not batch or batch[0] not in ('completed','archived','selection_done'):unfinished=True
        if unfinished:
            raise ValueError(f'{record["account"]} 有未完成批次，窗口或群绑定已改变；不能覆盖本批配置，请先核查旧批次')

    def save_many(self, records):
        """All selected bindings commit together; never partially replace them."""
        from group_navigation import navigation_entries
        from pinned_scan import path_key
        records=navigation_entries(records)
        for record in records:
            if len(record['account'])>100 or record['account']!=record['account'].strip():
                raise ValueError('账号备注长度须为 1–100 字，首尾不能含空格')
        paths=[path_key(r['window']) for r in records]
        if len(set(paths))!=len(paths):raise ValueError('同一程序路径有多个窗口，不能登记为不同账号')
        self.store._begin()
        try:
            rows=list(self.db.execute('SELECT account,window_json FROM pinned_group_observations'))
            rows+=list(self.db.execute('SELECT account,window_json FROM group_accounts WHERE window_json IS NOT NULL'))
            for record in records:
                self._protect_unfinished_binding(record)
                window=record['window'];account=record['account']
                for row in rows:
                    if row['account']==account:continue
                    bound=json.loads(row['window_json'])
                    if (path_key(bound)==path_key(window)
                            or all(bound.get(k)==window.get(k) for k in ('hwnd','pid','path'))):
                        raise ValueError(f'这个窗口／程序路径已记录为 {row["account"]}，请核对账号备注，不能交换旧账号')
                self._write_record(record)
            self.db.commit()
        except Exception:self.db.rollback();raise
        return [self.get(record['account']) for record in records]

    def _write_record(self,record):
        """Caller owns the transaction and must validate ownership/lifecycle."""
        account=record['account'];window=record['window']
        observation={k:v for k,v in record.items() if k not in ('window','account','saved')}
        self.db.execute('''INSERT INTO pinned_group_observations(account,window_json,observation_json)
            VALUES(?,?,?) ON CONFLICT(account) DO UPDATE SET window_json=excluded.window_json,
            observation_json=excluded.observation_json,saved=strftime('%Y-%m-%d %H:%M:%f','now')''',
            (account,json.dumps(window,ensure_ascii=False),json.dumps(observation,ensure_ascii=False)))
        self.store._event('pinned_groups_observed',detail=json.dumps({'account':account,
            'hwnd':window['hwnd'],'targets':[g['name'] for g in observation['targets']],
            'use_for_invitation':False},ensure_ascii=False))
