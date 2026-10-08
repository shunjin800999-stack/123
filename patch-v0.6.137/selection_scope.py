"""Current addition plan owns invitation lists; old contact records stay intact."""
import json


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def ensure_tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS contact_selection_scope(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),queue_id INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS contact_selection_scope_history(
        queue_id INTEGER PRIMARY KEY,previous_queue_id INTEGER,retired_json TEXT NOT NULL,
        saved TEXT DEFAULT CURRENT_TIMESTAMP)''')


def current_queue(db):
    if 'contact_selection_scope' not in tables(db):return None
    row=db.execute('SELECT queue_id FROM contact_selection_scope WHERE singleton=1').fetchone()
    return row[0] if row else None


def jobs(db):
    queue_id=current_queue(db)
    if queue_id is None:return []
    return [dict(account=r['account'],batch_id=r['batch_id'],window=json.loads(r['window_json']))
        for r in db.execute('''SELECT b.account,j.batch_id,j.window_json FROM contact_queue_jobs j
            JOIN batches b ON b.id=j.batch_id WHERE j.queue_id=? ORDER BY j.position''',(queue_id,))]


def batch_allowed(db,batch_id):
    queue_id=current_queue(db)
    return queue_id is None or bool(db.execute(
        'SELECT 1 FROM contact_queue_jobs WHERE queue_id=? AND batch_id=?',(queue_id,batch_id)).fetchone())


def require_batch(db,batch_id):
    if not batch_allowed(db,batch_id):
        raise ValueError('旧添加计划的选人名单已失效；请使用本次新添加计划的成功名单')


def record_allowed(db,record):
    if current_queue(db) is None:return True
    return any(job['account']==record['account'] and
        all(job['window'].get(k)==record['window'].get(k) for k in ('hwnd','pid','path'))
        for job in jobs(db))


def require_record(db,record):
    if not record_allowed(db,record):
        raise ValueError('这个账号窗口不属于本次添加计划，请按本次账号编号重新扫描置顶群')


def plan_allowed(db,plan):
    queue_id=current_queue(db)
    return queue_id is None or (plan.get('selection_queue_id')==queue_id
        and record_allowed(db,plan['record'])
        and (plan.get('source')!='confirmed_batch' or batch_allowed(db,plan['batch_id'])))


def stamp(db,plan):
    queue_id=current_queue(db)
    if queue_id is not None:plan['selection_queue_id']=queue_id
    return plan


def activate(store,queue_id):
    """Caller owns the start transaction; archive active lists and rebind by window."""
    db=store.db;previous=current_queue(db)
    if previous==queue_id:return
    names=tables(db)
    archived={name:[dict(r) for r in db.execute('SELECT * FROM '+name)] for name in (
        'pinned_member_plans','pinned_member_states','selection_carry_batches',
        'pinned_group_observations','pinned_group_navigation_states','group_accounts') if name in names}
    db.execute('INSERT INTO contact_selection_scope_history(queue_id,previous_queue_id,retired_json) VALUES(?,?,?)',
        (queue_id,previous,json.dumps(archived,ensure_ascii=False)))
    for name in ('pinned_member_plans','pinned_member_states','selection_carry_batches','pinned_group_navigation_states'):
        if name in names:db.execute('DELETE FROM '+name)
    # Reuse groups only for the exact still-open window, under its new label.
    if 'pinned_group_observations' in names:
        old=archived['pinned_group_observations']
        db.execute('DELETE FROM pinned_group_observations')
        for job in db.execute('''SELECT b.account,j.window_json FROM contact_queue_jobs j
                JOIN batches b ON b.id=j.batch_id WHERE j.queue_id=? ORDER BY j.position''',(queue_id,)).fetchall():
            window=json.loads(job['window_json'])
            matches=[row for row in old if all(json.loads(row['window_json']).get(k)==window.get(k)
                for k in ('hwnd','pid','path'))]
            if len(matches)==1:
                row=matches[0]
                db.execute('INSERT INTO pinned_group_observations(account,window_json,observation_json,saved) VALUES(?,?,?,?)',
                    (job['account'],json.dumps(window,ensure_ascii=False),row['observation_json'],row['saved']))
    # Preserve exported catalogs, retire their former window ownership.
    if 'group_accounts' in names:db.execute('UPDATE group_accounts SET window_json=NULL')
    db.execute('INSERT INTO contact_selection_scope(singleton,queue_id) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET queue_id=excluded.queue_id',
        (queue_id,))
    store._event('contact_selection_scope_started',detail=json.dumps(
        {'queue_id':queue_id,'previous_queue_id':previous,'old_invitation_lists_retired':True,
         'contact_records_changed':False,'numbers_reset':False},ensure_ascii=False))
