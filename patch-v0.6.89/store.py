"""Persistent work queue. This module never interacts with Telegram."""
from __future__ import annotations

import csv
import re
import sqlite3
from datetime import date
from pathlib import Path

LABELS = {
    'ready': '待处理', 'reserved': '已占用／待添加', 'added': '已添加联系人',
    'pending_invite': '等待人工邀请', 'completed': '已确认邀请成功',
    'username_not_found': '用户名不存在',
    'lookup_failed': '手机号添加失败／待核查', 'failed': '失败／保留核查', 'uncertain': '结果不明／待核查',
}


def csv_text(value):
    """Keep identifiers and free text from becoming spreadsheet formulas."""
    text = str(value) if value is not None else ''
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text


def normalize(raw: str, source: str) -> str:
    raw = raw.strip().lstrip('\ufeff')
    if source == 'phone':
        value = re.sub(r'[\s()\-]', '', raw)
        if not re.fullmatch(r'\+[1-9][0-9]{6,14}', value):
            raise ValueError('手机号需包含 + 和国家代码，使用 7–15 位数字')
        return value
    if source == 'username':
        value = raw.removeprefix('@')
        if not re.fullmatch(r'[A-Za-z0-9_]{1,32}', value):
            raise ValueError('用户名只接受字母、数字、下划线，最多 32 位')
        return '@' + value.lower()
    raise ValueError('未知名单类型')


def read_text_file(path: str) -> str:
    data = Path(path).read_bytes()
    if len(data) > 10 * 1024 * 1024:
        raise ValueError('名单超过 10 MB，请拆分文件')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError('无法识别文件编码，请另存为 UTF-8 TXT')


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.execute('PRAGMA journal_mode = WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY, account TEXT NOT NULL, target INTEGER NOT NULL,
            mode TEXT NOT NULL DEFAULT 'phone', status TEXT NOT NULL DEFAULT 'active',
            next_number INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '',
            created TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')));
          CREATE UNIQUE INDEX IF NOT EXISTS adding_account ON batches(account)
            WHERE status IN ('active','paused','review');
          DROP INDEX IF EXISTS active_account;
          CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, seq INTEGER NOT NULL,
            value TEXT NOT NULL, origin TEXT NOT NULL, origin_line INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready', batch_id INTEGER REFERENCES batches(id),
            account TEXT, contact_number INTEGER, reason TEXT NOT NULL DEFAULT '',
            updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),
            UNIQUE(source,value), UNIQUE(source,seq));
          CREATE UNIQUE INDEX IF NOT EXISTS account_contact_number
            ON items(account,contact_number) WHERE contact_number IS NOT NULL;
          CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),
            action TEXT NOT NULL, item_id INTEGER, batch_id INTEGER, detail TEXT NOT NULL);
        ''')
        self._init_global_numbering()

    def _init_global_numbering(self):
        columns={row['name'] for row in self.db.execute('PRAGMA table_info(items)')}
        if ('numbering_global' not in columns or 'added_at' not in columns) and (
                self.db.execute('SELECT COUNT(*) FROM items').fetchone()[0] or self.db.execute('SELECT COUNT(*) FROM batches').fetchone()[0]):
            backup_path=self.path.with_name(self.path.stem+'-before-global-numbering.sqlite3')
            if not backup_path.exists():self.backup(backup_path)
        self._begin()
        try:
            columns={row['name'] for row in self.db.execute('PRAGMA table_info(items)')}
            if 'numbering_global' not in columns:
                self.db.execute('ALTER TABLE items ADD COLUMN numbering_global INTEGER NOT NULL DEFAULT 0')
            if 'added_at' not in columns:self.db.execute('ALTER TABLE items ADD COLUMN added_at TEXT')
            self.db.execute('CREATE TABLE IF NOT EXISTS number_sequence (id INTEGER PRIMARY KEY CHECK(id=1),next_value INTEGER NOT NULL CHECK(next_value>=1))')
            high=self.db.execute('SELECT COALESCE(MAX(contact_number),0)+1 FROM items').fetchone()[0]
            old_next=self.db.execute('SELECT COALESCE(MAX(next_number),1) FROM batches').fetchone()[0]
            self.db.execute('INSERT OR IGNORE INTO number_sequence(id,next_value) VALUES(1,?)',(max(high,old_next),))
            self.db.execute('UPDATE number_sequence SET next_value=MAX(next_value,?) WHERE id=1',(high,))
            self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS global_contact_number ON items(contact_number) WHERE numbering_global=1 AND contact_number IS NOT NULL')
            # Updated timestamps may reflect a later invite; only an add event proves its date.
            self.db.execute("UPDATE items SET added_at=(SELECT MIN(time) FROM events WHERE item_id=items.id AND action='added') WHERE added_at IS NULL AND contact_number IS NOT NULL")
            self.db.execute('UPDATE batches SET next_number=(SELECT next_value FROM number_sequence WHERE id=1)')
            self.db.commit()
        except Exception:
            self.db.rollback();raise

    def next_contact_number(self):
        return self.db.execute('SELECT next_value FROM number_sequence WHERE id=1').fetchone()[0]

    def set_next_number(self,number):
        if type(number) is not int or not 1<=number<=999999:raise ValueError('下一个全局编号为 1–999999')
        self._begin()
        try:
            if number<self.next_contact_number():raise ValueError('全局编号只能提高，不能退回或重复使用')
            self.db.execute('UPDATE number_sequence SET next_value=? WHERE id=1',(number,))
            self.db.execute('UPDATE batches SET next_number=?',(number,))
            self._event('global_number_calibrated',detail=f'下一个全局编号={number}')
            self.db.commit()
        except Exception:
            self.db.rollback();raise

    def addition_stats(self,day=None):
        day=day or self.db.execute("SELECT date('now','localtime')").fetchone()[0]
        date.fromisoformat(day)
        today=self.db.execute("SELECT COUNT(*) FROM items WHERE contact_number IS NOT NULL AND date(added_at,'localtime')=?",(day,)).fetchone()[0]
        total=self.db.execute('SELECT COUNT(*) FROM items WHERE contact_number IS NOT NULL').fetchone()[0]
        unknown=self.db.execute('SELECT COUNT(*) FROM items WHERE contact_number IS NOT NULL AND added_at IS NULL').fetchone()[0]
        return {'day':day,'today_added':today,'total_added':total,'unknown_add_date':unknown,'next_number':self.next_contact_number()}

    def close(self):
        self.db.close()

    def _event(self, action, item_id=None, batch_id=None, detail=''):
        self.db.execute('INSERT INTO events(action,item_id,batch_id,detail) VALUES(?,?,?,?)',
                        (action, item_id, batch_id, detail))

    def _begin(self):
        self.db.execute('BEGIN IMMEDIATE')

    def import_text(self, source: str, text: str, origin='粘贴输入'):
        result = {'new': 0, 'duplicate': 0, 'invalid': [], 'duplicates': [], 'within_batch': 0}
        input_seen = set()
        self._begin()
        try:
            seq = self.db.execute('SELECT COALESCE(MAX(seq),0) FROM items WHERE source=?',
                                  (source,)).fetchone()[0]
            for line, raw in enumerate(text.splitlines(), 1):
                if not raw.strip().lstrip('\ufeff'):
                    continue
                try:
                    value = normalize(raw, source)
                except ValueError as error:
                    result['invalid'].append((line, str(error)))
                    self._event('import_invalid', detail=f'{source} {origin} 第{line}行：{error}')
                    continue
                exists = self.db.execute('SELECT * FROM items WHERE source=? AND value=?',
                                         (source, value)).fetchone()
                if exists:
                    result['duplicate'] += 1
                    within = value in input_seen
                    result['within_batch'] += int(within)
                    result['duplicates'].append({'line':line, 'value':value, 'within_batch':within,
                                                 'status':exists['status'], 'account':exists['account'] or '',
                                                 'seq':exists['seq']})
                    input_seen.add(value)
                    continue
                seq += 1
                self.db.execute('INSERT INTO items(source,seq,value,origin,origin_line) VALUES(?,?,?,?,?)',
                                (source, seq, value, origin, line))
                result['new'] += 1
                input_seen.add(value)
            self._event('import', detail=f'{source}: 新增{result["new"]}，重复{result["duplicate"]}，错误{len(result["invalid"])}')
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    def create_batch(self, account, target=20, start=None):
        account = account.strip()
        if not account or len(account) > 100:
            raise ValueError('请填写 1–100 字的账号备注')
        if not 1 <= target <= 40:raise ValueError('每批人数为 1–40')
        self._begin()
        try:
            old = self.db.execute("SELECT id FROM batches WHERE account=? AND status IN ('active','waiting','paused','review')",
                                  (account,)).fetchone()
            if old:
                raise ValueError(f'该账号已有未结束批次 #{old[0]}，请先核查并完成')
            number=self.next_contact_number()
            if start is not None and start!=number:raise ValueError('所有账号共用全局编号；请使用全局起点校准，不再设置每批起点')
            cur = self.db.execute('INSERT INTO batches(account,target,next_number) VALUES(?,?,?)',
                                  (account, target, number))
            batch_id = cur.lastrowid
            self._event('batch_created', batch_id=batch_id, detail=account)
            self.db.commit()
            return batch_id
        except Exception:
            self.db.rollback()
            raise

    def batch(self, batch_id):
        row = self.db.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
        if not row:
            raise ValueError('找不到该批次')
        result=dict(row);result['next_number']=self.next_contact_number();return result

    def reserve_next(self, batch_id, *, phone_only=False, start_phone_seq=1, username_only=False, start_username_seq=1):
        """Reserve before performing an action. Never blindly retry a held item."""
        if type(start_phone_seq) is not int or start_phone_seq<1 or type(start_username_seq) is not int or start_username_seq<1:
            raise ValueError('起始名单序号必须为正整数')
        if phone_only and username_only:raise ValueError('名单类型冲突')
        self._begin()
        try:
            batch = self.batch(batch_id)
            if batch['status'] != 'active':
                raise ValueError('批次不在可处理状态，请核查暂停或待确认原因')
            held = self.db.execute("SELECT * FROM items WHERE batch_id=? AND status IN ('reserved','uncertain') ORDER BY id LIMIT 1",
                                   (batch_id,)).fetchone()
            if held:
                self.db.commit()
                return dict(held), False
            count = self.db.execute('SELECT COUNT(*) FROM items WHERE batch_id=? AND contact_number IS NOT NULL',
                                    (batch_id,)).fetchone()[0]
            if count >= batch['target']:
                self.db.commit()
                return None, False
            row = self.db.execute("SELECT * FROM items WHERE source=? AND status='ready' AND seq>=? ORDER BY seq LIMIT 1",
                                  ('username' if username_only else 'phone' if phone_only else batch['mode'],start_username_seq if username_only or (not phone_only and batch['mode']=='username') else start_phone_seq)).fetchone()
            if row is None and batch['mode'] == 'phone' and not phone_only and not username_only:
                self.db.execute("UPDATE batches SET mode='username' WHERE id=?", (batch_id,))
                self._event('source_switch', batch_id=batch_id, detail='手机号名单已用尽')
                row = self.db.execute("SELECT * FROM items WHERE source='username' AND status='ready' ORDER BY seq LIMIT 1").fetchone()
            if row:
                self.db.execute("UPDATE items SET status='reserved',batch_id=?,account=?,updated=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=? AND status='ready'",
                                (batch_id, batch['account'], row['id']))
                self._event('reserved', row['id'], batch_id, '尚未添加联系人')
                row = self.db.execute('SELECT * FROM items WHERE id=?', (row['id'],)).fetchone()
            self.db.commit()
            return (dict(row), True) if row else (None, False)
        except Exception:
            self.db.rollback()
            raise

    def record_add_result(self, item_id, outcome, reason='', *, expected_phone=None, expected_number=None):
        """Only a confirmed add assigns a contact number; limits always pause."""
        if outcome not in ('added', 'ordinary_failure', 'restriction', 'uncertain'):
            raise ValueError('未知添加结果')
        self._begin()
        try:
            row = self.db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            if not row or row['status'] != 'reserved':
                raise ValueError('仅可记录已占用且尚未添加的用户')
            batch = self.batch(row['batch_id'])
            if batch['status'] != 'active':
                raise ValueError('批次已暂停或结束')
            if expected_phone is not None or expected_number is not None:
                if outcome != 'added' or row['source'] != 'phone' or row['value'] != expected_phone:
                    raise ValueError('核验手机号与已占用记录不一致，未更新')
                if self.next_contact_number() != expected_number:
                    raise ValueError('核验期间全局编号已改变，未更新；请重新核查')
            number = None
            if outcome == 'added':
                if (self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pinned_batch_member_plans'").fetchone()
                        and self.db.execute('SELECT 1 FROM pinned_batch_member_plans WHERE batch_id=?',(batch['id'],)).fetchone()):
                    raise ValueError('本批两群名单已冻结，不能继续增加联系人；完成本批后建立新批次')
                number = self.next_contact_number()
                if number>999999:raise ValueError('全局备注编号已超过 999999，停止，未分配')
                status = 'added'
                self.db.execute('UPDATE number_sequence SET next_value=next_value+1 WHERE id=1')
                self.db.execute('UPDATE batches SET next_number=?',(number+1,))
            elif outcome == 'ordinary_failure':
                status = 'failed'
                if row['source'] == 'phone':
                    self.db.execute("UPDATE batches SET mode='username' WHERE id=?", (batch['id'],))
                    self._event('source_switch', batch_id=batch['id'], detail='手机号普通失败，立即切换用户名')
            else:
                status = 'uncertain'
                self.db.execute('UPDATE batches SET status=?,reason=? WHERE id=?',
                                ('paused' if outcome == 'restriction' else 'review', reason or outcome, batch['id']))
            self.db.execute("UPDATE items SET status=?,contact_number=?,reason=?,updated=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",
                            (status, number, reason, item_id))
            if outcome=='added':
                self.db.execute("UPDATE items SET numbering_global=1,added_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(item_id,))
            if outcome=='added' and self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contact_queue_jobs'").fetchone():
                linked=list(self.db.execute("""SELECT j.id,j.queue_id,j.preview_number,q.state AS queue_state
                    FROM contact_queue_jobs j JOIN contact_queues q ON q.id=j.queue_id
                    WHERE j.item_id=? AND j.state NOT IN ('target_reached','no_list','paused')""",(item_id,)))
                for job in linked:
                    if job['queue_state']=='running':
                        raise ValueError('原联系人仍在运行队列中，请先停止队列后核查登记')
                    if job['queue_state'] not in ('review','stopped'):continue
                    if job['preview_number'] is not None and job['preview_number']!=number:
                        raise ValueError('原表单备注与待登记编号不一致，请核查，未登记')
                    self.db.execute("UPDATE contact_queue_jobs SET state='manual_added_pending_profile',preview_number=?,reason='使用记录已手动登记成功，等待资料页核验恢复队列' WHERE id=?",(number,job['id']))
                    self.db.execute("UPDATE contact_queues SET state='review',reason='使用记录已登记成功；请保留对应数字备注资料页，点击核验已提交资料页并继续，不要重复提交' WHERE id=?",(job['queue_id'],))
                    self._event('contact_queue_manual_added_pending',item_id,batch['id'],str(job['queue_id']))
            self._event(outcome, item_id, batch['id'], reason)
            self.db.commit()
            return number
        except Exception:
            self.db.rollback()
            raise

    def mark_waiting(self, batch_id):
        self._begin()
        try:
            if self.batch(batch_id)['status'] != 'active':
                raise ValueError('该批次不可进入待邀请状态')
            held = self.db.execute("SELECT COUNT(*) FROM items WHERE batch_id=? AND status IN ('reserved','uncertain')",
                                   (batch_id,)).fetchone()[0]
            added = self.db.execute("SELECT COUNT(*) FROM items WHERE batch_id=? AND status='added'", (batch_id,)).fetchone()[0]
            if held or not added:
                raise ValueError('请先核查已占用用户，并至少确认一位联系人添加成功')
            self.db.execute("UPDATE items SET status='pending_invite' WHERE batch_id=? AND status='added'", (batch_id,))
            self.db.execute("UPDATE batches SET status='waiting' WHERE id=?", (batch_id,))
            self._event('waiting_for_manual_invite', batch_id=batch_id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def confirm_invited(self, item_ids):
        if not item_ids:
            raise ValueError('请先选择已核实邀请成功的用户')
        self._begin()
        try:
            batches = set()
            for item_id in item_ids:
                row = self.db.execute('SELECT status,batch_id FROM items WHERE id=?', (item_id,)).fetchone()
                if not row or row['status'] != 'pending_invite':
                    raise ValueError('只能确认处于等待人工邀请状态的用户')
                if (self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='batch_group_plans'").fetchone()
                        and self.db.execute('SELECT 1 FROM batch_group_plans WHERE batch_id=?',(row['batch_id'],)).fetchone()):
                    raise ValueError('此批次有两个目标群，旧版单群确认不能标记全部完成；两群结果登记将在群界面核对接入后提供')
                if (self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pinned_batch_member_plans'").fetchone()
                        and self.db.execute('SELECT 1 FROM pinned_batch_member_plans WHERE batch_id=?',(row['batch_id'],)).fetchone()):
                    raise ValueError('此批次用于两个置顶群，请在账号群聊页分别确认两群全部邀请成功')
                self.db.execute("UPDATE items SET status='completed',updated=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?", (item_id,))
                self._event('manual_invite_confirmed', item_id, row['batch_id'])
                batches.add(row['batch_id'])
            for batch_id in batches:
                pending = self.db.execute("SELECT COUNT(*) FROM items WHERE batch_id=? AND status='pending_invite'", (batch_id,)).fetchone()[0]
                if pending == 0:
                    self.db.execute("UPDATE batches SET status='completed' WHERE id=?", (batch_id,))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def rows(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM items ORDER BY source,seq')]

    def batches(self):
        number=self.next_contact_number()
        return [dict(row,next_number=number) for row in self.db.execute('SELECT * FROM batches ORDER BY id DESC')]

    def progress(self, source):
        rows = [row for row in self.rows() if row['source'] == source]
        counts = {status: sum(row['status'] == status for row in rows) for status in LABELS}
        next_row = next((row for row in rows if row['status'] == 'ready'), None)
        return {'counts': counts, 'total': len(rows), 'next': next_row,
                'allocated_to': max((row['seq'] for row in rows if row['batch_id'] is not None), default=0)}

    def export_csv(self, path):
        with open(path, 'w', newline='', encoding='utf-8-sig') as output:
            writer = csv.writer(output)
            writer.writerow(['名单类型','名单序号','用户信息','原文件','原行号','账号备注','联系人备注编号','状态','原因','添加确认时间UTC'])
            for row in self.rows():
                number = '' if row['contact_number'] is None else str(row['contact_number'])
                writer.writerow([csv_text(value) for value in [
                    '手机号' if row['source']=='phone' else '用户名',row['seq'],
                    row['value'],row['origin'],row['origin_line'],row['account'] or '',
                    number,LABELS[row['status']],row['reason'],row['added_at'] or '']])

    def export_events(self, path):
        with open(path, 'w', newline='', encoding='utf-8-sig') as output:
            writer = csv.writer(output)
            writer.writerow(['记录号','时间UTC','操作','用户记录号','批次号','说明'])
            for row in self.db.execute('SELECT * FROM events ORDER BY id'):
                writer.writerow([csv_text(value) for value in row])

    def backup(self, path):
        destination = Path(path)
        if destination.resolve() == self.path.resolve():
            raise ValueError('备份文件不能覆盖正在使用的数据文件')
        with sqlite3.connect(destination) as output:
            self.db.backup(output)
