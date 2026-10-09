"""Transactional account-label migration; window identities and numbers stay intact."""
import json
import re
import sqlite3


def migrate_account_names(store):
    db=store.db
    tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    schemas={t:list(db.execute(f'PRAGMA table_info("{t}")')) for t in tables}
    names=set()
    for t,cols in schemas.items():
        if any(c['name']=='account' for c in cols):
            names.update(r[0] for r in db.execute(f'SELECT DISTINCT account FROM "{t}" WHERE account IS NOT NULL'))
    numeric={int(n[2:]) for n in names if re.fullmatch(r'账号[1-9][0-9]*',n)}
    old=sorted((n for n in names if re.fullmatch(r'账号(?:[A-Z]+|0[0-9]+)',n)),key=lambda n:(len(n),n))
    mapping={};number=1
    for name in old:
        while number in numeric:number+=1
        mapping[name]=f'账号{number}';numeric.add(number);number+=1
    legacy=db.execute("SELECT COUNT(*) FROM items WHERE status='failed' AND id IN (SELECT item_id FROM events WHERE action='phone_not_registered')").fetchone()[0]
    if not mapping and not legacy:return {}
    # A consistent backup includes WAL data and precedes all modifications.
    backup=store.path.with_name(store.path.stem+'-before-v0658.sqlite3')
    if not backup.exists():
        dest=sqlite3.connect(backup)
        try:db.backup(dest)
        finally:dest.close()
    def rewrite(value):
        if isinstance(value,list):return [rewrite(x) for x in value]
        if isinstance(value,dict):
            return {mapping.get(k,k):mapping.get(v,v) if k in ('account','account_label') and isinstance(v,str) else rewrite(v) for k,v in value.items()}
        return value
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('PRAGMA defer_foreign_keys=ON')
        for t,cols in schemas.items():
            if any(c['name']=='account' for c in cols):
                for oldname,newname in mapping.items():
                    db.execute(f'UPDATE "{t}" SET account=? WHERE account=?',(newname,oldname))
            for col in cols:
                name=col['name']
                if not name.endswith('_json'):continue
                for row in list(db.execute(f'SELECT rowid,"{name}" FROM "{t}" WHERE "{name}" IS NOT NULL')):
                    try:value=json.loads(row[1])
                    except (ValueError,TypeError):continue
                    changed=rewrite(value)
                    if changed!=value:db.execute(f'UPDATE "{t}" SET "{name}"=? WHERE rowid=?',(json.dumps(changed,ensure_ascii=False),row[0]))
        db.execute("UPDATE items SET status='lookup_failed',reason='历史手机号添加失败，待核查；原弹窗不能证明未注册' WHERE status='failed' AND id IN (SELECT item_id FROM events WHERE action='phone_not_registered')")
        store._event('account_labels_migrated',detail=json.dumps(mapping,ensure_ascii=False))
    return mapping
