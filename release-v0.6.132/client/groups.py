"""Account-owned group catalog and two explicit targets, without invitations.

Telegram Desktop JSON export schema is documented by its JsonWriter source.
Only chats.list group metadata is retained, never messages or left_chats.
UIA probes are diagnostics, not proof of a complete account dialog catalog.
"""
import hashlib
import json
from pathlib import Path

from windows_scan import is_telegram_process


GROUP_TYPES = {'private_group':'普通群', 'private_supergroup':'私有超级群', 'public_supergroup':'公开超级群'}
MAX_EXPORT_BYTES = 64 * 1024 * 1024


def positive_id(value):
    if type(value) is not int or not 0 < value <= 9223372036854775807:
        raise ValueError('账号或群 ID 必须为有效正整数')
    return str(value)


def parse_group_export(data):
    if not isinstance(data, dict):raise ValueError('请使用该账号导出的完整 result.json')
    personal = data.get('personal_information')
    chats = data.get('chats')
    if not isinstance(personal,dict) or not isinstance(chats,dict) or not isinstance(chats.get('list'),list):
        raise ValueError('缺少账号信息或 chats.list；请勾选个人信息及群聊并使用 JSON 格式，不是单个聊天记录导出')
    owner_id = positive_id(personal.get('user_id'))
    label = ' '.join(str(personal.get(k) or '').strip() for k in ('first_name','last_name')).strip()
    groups = {}; counts = {'channels':0,'other_chats':0,'unknown_types':0,'duplicate_rows':0,'unnamed_groups':0}
    known_others = {'personal_chat','bot_chat','saved_messages','replies','verification_codes'}
    for row in chats['list']:
        if not isinstance(row,dict):raise ValueError('聊天目录结构不完整，未导入')
        kind = row.get('type')
        if kind is not None and not isinstance(kind,str):raise ValueError('聊天类型字段异常，未导入')
        if kind in ('private_channel','public_channel'):
            counts['channels'] += 1;continue
        if kind not in GROUP_TYPES:
            counts['other_chats' if kind in known_others else 'unknown_types'] += 1;continue
        chat_id = positive_id(row.get('id'))
        name = row.get('name')
        if name is None:name = ''
        if not isinstance(name,str):raise ValueError('群名称字段异常，未导入')
        name = name.strip()
        if not name:counts['unnamed_groups'] += 1
        namespace = 'group' if kind == 'private_group' else 'supergroup'
        key = namespace + ':' + chat_id
        item = {'peer_key':key,'chat_id':chat_id,'name':name,'type':kind}
        if key in groups:
            if groups[key] != item:raise ValueError(f'同一个群 ID 出现冲突记录：{key}，未导入')
            counts['duplicate_rows'] += 1
        else:groups[key] = item
    return {'owner_id':owner_id,'owner_label':label,'groups':list(groups.values()),
        'chat_count':len(chats['list']),'group_count':len(groups),'counts':counts,
        'coverage':'export_snapshot','live_membership_verified':False,'all_groups_verified':False}


def read_group_export(path):
    path = Path(path)
    if path.stat().st_size > MAX_EXPORT_BYTES:
        raise ValueError('JSON 超过 64 MB；请仅导出个人信息、私有群及公开群，不选媒体，再重试')
    raw = path.read_bytes()
    if len(raw) > MAX_EXPORT_BYTES:raise ValueError('JSON 超过 64 MB，未导入')
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeDecodeError,json.JSONDecodeError,RecursionError):
        raise ValueError('文件不是完整 UTF-8 JSON；请等待 Telegram 导出成功再读取 result.json') from None
    result = parse_group_export(data)
    result.update(source=path.name, sha256=hashlib.sha256(raw).hexdigest())
    return result


def valid_window(window):
    return (isinstance(window,dict) and type(window.get('hwnd')) is int and window['hwnd']>0
        and type(window.get('pid')) is int and window['pid']>0 and window.get('candidate') is True
        and window.get('minimized') is False and is_telegram_process(window.get('path')))


class GroupCatalog:
    def __init__(self, store):
        self.store=store;self.db=store.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS group_accounts (
                account TEXT PRIMARY KEY, owner_id TEXT NOT NULL UNIQUE,
                owner_label TEXT NOT NULL, window_json TEXT, latest_import INTEGER);
            CREATE TABLE IF NOT EXISTS group_imports (
                id INTEGER PRIMARY KEY, account TEXT NOT NULL REFERENCES group_accounts(account),
                source TEXT NOT NULL, sha256 TEXT NOT NULL, summary_json TEXT NOT NULL,
                imported TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')));
            CREATE TABLE IF NOT EXISTS account_groups (
                account TEXT NOT NULL REFERENCES group_accounts(account), peer_key TEXT NOT NULL,
                chat_id TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL,
                present INTEGER NOT NULL, import_id INTEGER NOT NULL REFERENCES group_imports(id),
                PRIMARY KEY(account,peer_key));
            CREATE TABLE IF NOT EXISTS account_group_targets (
                account TEXT PRIMARY KEY REFERENCES group_accounts(account),
                first_peer TEXT NOT NULL, second_peer TEXT NOT NULL,
                saved TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),
                CHECK(first_peer<>second_peer),
                FOREIGN KEY(account,first_peer) REFERENCES account_groups(account,peer_key),
                FOREIGN KEY(account,second_peer) REFERENCES account_groups(account,peer_key));
            CREATE TABLE IF NOT EXISTS batch_group_plans (
                batch_id INTEGER PRIMARY KEY REFERENCES batches(id), account TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                created TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')));
        ''')

    def import_file(self, account, path):
        account=account.strip()
        if not account or len(account)>100:raise ValueError('请填写一直使用的稳定账号备注，长度 1–100 字')
        parsed=read_group_export(path)
        self.store._begin()
        try:
            old=self.db.execute('SELECT * FROM group_accounts WHERE account=?',(account,)).fetchone()
            if old and old['owner_id']!=parsed['owner_id']:
                raise ValueError('这个账号备注已绑定另一个 Telegram 账号 ID；未覆盖旧群配置，请核对文件和账号')
            other=self.db.execute('SELECT account FROM group_accounts WHERE owner_id=? AND account<>?',(parsed['owner_id'],account)).fetchone()
            if other:raise ValueError(f'此 Telegram 账号已登记为 {other[0]}；请沿用该账号备注')
            self.db.execute('INSERT INTO group_accounts(account,owner_id,owner_label) VALUES(?,?,?) ON CONFLICT(account) DO UPDATE SET owner_label=excluded.owner_label',
                (account,parsed['owner_id'],parsed['owner_label']))
            summary={key:value for key,value in parsed.items() if key!='groups'}
            import_id=self.db.execute('INSERT INTO group_imports(account,source,sha256,summary_json) VALUES(?,?,?,?)',
                (account,parsed['source'],parsed['sha256'],json.dumps(summary,ensure_ascii=False))).lastrowid
            self.db.execute('UPDATE account_groups SET present=0 WHERE account=?',(account,))
            for group in parsed['groups']:
                self.db.execute('INSERT INTO account_groups(account,peer_key,chat_id,name,type,present,import_id) VALUES(?,?,?,?,?,1,?) ON CONFLICT(account,peer_key) DO UPDATE SET name=excluded.name,type=excluded.type,present=1,import_id=excluded.import_id',
                    (account,group['peer_key'],group['chat_id'],group['name'],group['type'],import_id))
            self.db.execute('UPDATE group_accounts SET latest_import=? WHERE account=?',(import_id,account))
            self.store._event('group_catalog_imported',detail=f'{account}；账号ID {parsed["owner_id"]}；导出记录群数 {parsed["group_count"]}；未证明实时完整目录')
            self.db.commit()
            return self.catalog(account)
        except Exception:self.db.rollback();raise

    def accounts(self):
        return [dict(r) for r in self.db.execute('SELECT account,owner_id,owner_label FROM group_accounts ORDER BY account')]

    def bind_window(self, account, window):
        if not valid_window(window):raise ValueError('请选择已还原的 Telegram.exe 候选窗口')
        self.store._begin()
        try:
            for row in self.db.execute('SELECT account,window_json FROM group_accounts WHERE account<>? AND window_json IS NOT NULL',(account,)):
                bound=json.loads(row['window_json'])
                if all(bound.get(k)==window.get(k) for k in ('hwnd','pid','path')):
                    raise ValueError(f'这个窗口已绑定为 {row["account"]}，请核对账号，不能同时绑定另一个账号')
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pinned_group_observations'").fetchone():
                for row in self.db.execute('SELECT account,window_json FROM pinned_group_observations WHERE account<>?',(account,)):
                    bound=json.loads(row['window_json'])
                    if all(bound.get(k)==window.get(k) for k in ('hwnd','pid','path')):
                        raise ValueError(f'这个窗口已有 {row["account"]} 的置顶群记录，请核对账号备注')
            changed=self.db.execute('UPDATE group_accounts SET window_json=? WHERE account=?',
                (json.dumps(window,ensure_ascii=False),account)).rowcount
            if not changed:raise ValueError('先导入该账号的群目录，再绑定窗口')
            self.store._event('group_account_window_bound',detail=f'{account}；人工核对窗口 0x{window["hwnd"]:X}；未通过窗口标题验证账号ID')
            self.db.commit()
        except Exception:self.db.rollback();raise

    def catalog(self, account):
        owner=self.db.execute('SELECT * FROM group_accounts WHERE account=?',(account,)).fetchone()
        if not owner:return None
        result=dict(owner)
        result['window']=json.loads(result.pop('window_json')) if owner['window_json'] else None
        imported=self.db.execute('SELECT * FROM group_imports WHERE id=?',(owner['latest_import'],)).fetchone()
        result['summary']=json.loads(imported['summary_json']);result['imported']=imported['imported']
        result['groups']=[dict(r) for r in self.db.execute('SELECT * FROM account_groups WHERE account=? AND present=1 ORDER BY rowid',(account,))]
        target=self.db.execute('SELECT * FROM account_group_targets WHERE account=?',(account,)).fetchone()
        result['target_keys']=[] if not target else [target['first_peer'],target['second_peer']]
        result['targets']=[];result['targets_ready']=False
        if target:
            by_key={g['peer_key']:g for g in result['groups']}
            result['targets']=[by_key[key] for key in result['target_keys'] if key in by_key]
            result['targets_ready']=len(result['targets'])==2 and all(g['name'] for g in result['targets'])
        return result

    def save_targets(self, account, keys):
        keys=list(keys)
        if len(keys)!=2 or len(set(keys))!=2:raise ValueError('请恰好选择两个不同的目标群')
        self.store._begin()
        try:
            catalog=self.catalog(account)
            by_key={} if not catalog else {g['peer_key']:g for g in catalog['groups']}
            if any(key not in by_key or not by_key[key]['name'] for key in keys):
                raise ValueError('所选群必须属于该账号的最新目录，且有可核对的名称')
            self.db.execute('INSERT INTO account_group_targets(account,first_peer,second_peer) VALUES(?,?,?) ON CONFLICT(account) DO UPDATE SET first_peer=excluded.first_peer,second_peer=excluded.second_peer,saved=strftime(\'%Y-%m-%d %H:%M:%f\',\'now\')',
                (account,keys[0],keys[1]))
            self.store._event('group_targets_saved',detail=json.dumps({'account':account,'targets':keys},ensure_ascii=False))
            self.db.commit()
        except Exception:self.db.rollback();raise

    def queue_targets(self, account, window):
        catalog=self.catalog(account)
        if not catalog or not catalog['targets_ready']:raise ValueError(f'{account} 尚未设置两个有效目标群；先完成账号群聊配置')
        bound=catalog['window']
        if not bound or any(bound.get(k)!=window.get(k) for k in ('hwnd','pid','path')):
            raise ValueError(f'{account} 的窗口绑定不匹配；请在账号群聊页核对并重新绑定')
        return [{'peer_key':g['peer_key'],'chat_id':g['chat_id'],'name':g['name'],'type':g['type'],
            'owner_id':catalog['owner_id'],'live_membership_verified':False} for g in catalog['targets']]

    def plan_for_batch(self, batch_id):
        existing=self.db.execute('SELECT plan_json FROM batch_group_plans WHERE batch_id=?',(batch_id,)).fetchone()
        if existing:return json.loads(existing[0])
        batch=self.store.batch(batch_id);catalog=self.catalog(batch['account'])
        if not catalog or not catalog['targets_ready']:raise ValueError('本批账号还没有两个有效目标群')
        members=[{'item_id':r['id'],'number':str(r['contact_number'])} for r in self.db.execute(
            'SELECT id,contact_number FROM items WHERE batch_id=? AND contact_number IS NOT NULL ORDER BY contact_number',(batch_id,))]
        if not members:raise ValueError('本批尚未确认添加成功的联系人，不生成邀请名单')
        if batch['status']=='active' and len(members)<batch['target']:
            raise ValueError('本批还未达到计划人数且未暂停；先完成添加，再冻结本批两群名单')
        targets=catalog['targets']
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contact_queue_jobs'").fetchone():
            columns={r['name'] for r in self.db.execute('PRAGMA table_info(contact_queue_jobs)')}
            if 'targets_json' in columns:
                queued=self.db.execute('SELECT targets_json FROM contact_queue_jobs WHERE batch_id=? ORDER BY id DESC LIMIT 1',(batch_id,)).fetchone()
                if queued and json.loads(queued[0]):
                    targets=json.loads(queued[0])
                    if any('peer_key' not in g or 'owner_id' not in g for g in targets):
                        raise ValueError('本批使用置顶群配置，请在“置顶群”页从当前批次生成两群名单')
        plan={'account':batch['account'],'owner_id':catalog['owner_id'],'batch_id':batch_id,
            'contact_count':len(members),'final_invite_clicked':False,'scope':'two_group_plan',
            'groups':[{'slot':i,'peer_key':g['peer_key'],'chat_id':g['chat_id'],'name':g['name'],'type':g['type'],
                'members':[dict(m) for m in members],'state':'not_started','live_group_verified':False} for i,g in enumerate(targets,1)]}
        with self.db:
            self.db.execute('INSERT INTO batch_group_plans(batch_id,account,plan_json) VALUES(?,?,?)',
                (batch_id,batch['account'],json.dumps(plan,ensure_ascii=False)))
            self.store._event('two_group_plan_saved',batch_id=batch_id,detail=f'同批 {len(members)} 位联系人用于两个群；未选择或邀请')
        return plan

    def export(self, account, path):
        catalog=self.catalog(account)
        if not catalog:raise ValueError('还没有该账号的群目录')
        catalog.update(scope='account_group_configuration',final_invite_clicked=False)
        catalog['batch_plans']=[json.loads(r[0]) for r in self.db.execute('SELECT plan_json FROM batch_group_plans WHERE account=? ORDER BY batch_id',(account,))]
        Path(path).write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8')
