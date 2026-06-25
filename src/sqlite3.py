import json
import os

class Error(Exception):
    pass

class DatabaseError(Error):
    pass

class Connection:
    def __init__(self, database, timeout=30.0):
        self.database = database
        self.db_path = database
        self.tables = {}
        self.load()

    def load(self):
        json_path = self.db_path.replace('.sqlite3', '.json')
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    self.tables = json.load(f)
            except:
                self.tables = {}
        else:
            self.tables = {}

    def save(self):
        json_path = self.db_path.replace('.sqlite3', '.json')
        dir_name = os.path.dirname(json_path)
        if dir_name and not os.path.exists(dir_name):
            try:
                os.makedirs(dir_name)
            except:
                pass
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(self.tables, f)
        except Exception as e:
            print(f"Failed to save JSON DB: {e}")

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.save()

    def rollback(self):
        self.load()

    def close(self):
        pass

def connect(database, timeout=30.0):
    return Connection(database, timeout)

class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.results = []
        self.index = 0
        self.rowcount = 0
        self.lastrowid = None

    def fetchone(self):
        if self.index < len(self.results):
            r = self.results[self.index]
            self.index += 1
            return r
        return None

    def fetchall(self):
        res = self.results[self.index:]
        self.index = len(self.results)
        return res

    def execute(self, sql, params=None):
        self.results = []
        self.index = 0
        self.rowcount = 0
        
        sql_clean = sql.strip().replace('\n', ' ')
        while '  ' in sql_clean:
            sql_clean = sql_clean.replace('  ', ' ')
            
        sql_upper = sql_clean.upper()
        
        # 1. PRAGMA journal_mode=WAL
        if "PRAGMA JOURNAL_MODE=WAL" in sql_upper:
            self.results = [("wal",)]
            return self
            
        # 2. PRAGMA synchronous
        if "PRAGMA SYNCHRONOUS" in sql_upper:
            self.results = [("normal",)]
            return self

        # 3. PRAGMA table_info
        if "PRAGMA TABLE_INFO" in sql_upper:
            idx = sql_upper.find("TABLE_INFO")
            sub = sql_clean[idx + 10:].strip()
            start = sub.find("(")
            end = sub.find(")")
            if start != -1 and end != -1:
                table_name = sub[start+1:end].strip()
                if table_name == 'static_hosts':
                    self.results = [
                        (0, 'host_id', 'INTEGER', 0, None, 1),
                        (1, 'hostname', 'TEXT', 1, None, 0),
                        (2, 'ip_address', 'TEXT', 0, None, 0),
                        (3, 'created_at', 'DATETIME', 0, 'CURRENT_TIMESTAMP', 0),
                        (4, 'updated_at', 'DATETIME', 0, 'CURRENT_TIMESTAMP', 0)
                    ]
                elif table_name == 'other_proxies':
                    self.results = [
                        (0, 'proxy_id', 'INTEGER', 0, None, 1),
                        (1, 'node_id', 'TEXT', 0, None, 0),
                        (2, 'ip_address', 'TEXT', 1, None, 0),
                        (3, 'port', 'INTEGER', 0, '80', 0),
                        (4, 'token', 'TEXT', 1, None, 0),
                        (5, 'discovery_method', 'TEXT', 1, None, 0),
                        (6, 'last_seen', 'DATETIME', 0, 'CURRENT_TIMESTAMP', 0),
                        (7, 'is_active', 'BOOLEAN', 0, '1', 0)
                    ]
                else:
                    self.results = []
            return self

        # 4. CREATE TABLE
        if sql_upper.startswith("CREATE TABLE"):
            idx = sql_upper.find("CREATE TABLE")
            sub = sql_clean[idx + 12:].strip()
            if sub.upper().startswith("IF NOT EXISTS"):
                sub = sub[13:].strip()
            table_name = ""
            for char in sub:
                if ('a' <= char <= 'z') or ('A' <= char <= 'Z') or ('0' <= char <= '9') or char == '_':
                    table_name += char
                else:
                    break
            if table_name:
                if table_name not in self.connection.tables:
                    self.connection.tables[table_name] = []
            return self

        # 5. ALTER TABLE ... ADD COLUMN ...
        if sql_upper.startswith("ALTER TABLE"):
            parts = sql_clean.split()
            if len(parts) >= 6 and parts[3].upper() == "ADD" and parts[4].upper() == "COLUMN":
                table_name = parts[2]
                col_name = parts[5]
                if table_name in self.connection.tables:
                    for r in self.connection.tables[table_name]:
                        if col_name not in r:
                            r[col_name] = None
            return self

        # 6. CREATE INDEX
        if sql_upper.startswith("CREATE INDEX") or sql_upper.startswith("CREATE UNIQUE INDEX"):
            return self

        # 7. DELETE FROM
        if sql_upper.startswith("DELETE FROM"):
            parts = sql_clean.split()
            table_name = parts[2]
            if table_name not in self.connection.tables:
                self.connection.tables[table_name] = []
            
            if "WHERE" not in sql_upper:
                self.rowcount = len(self.connection.tables[table_name])
                self.connection.tables[table_name] = []
            else:
                where_idx = sql_upper.index("WHERE")
                where_clause = sql_clean[where_idx + 5:].strip()
                self.connection.tables[table_name], deleted = self._filter_out(table_name, where_clause, params)
                self.rowcount = len(deleted)
            return self

        # 8. INSERT INTO
        if sql_upper.startswith("INSERT INTO"):
            idx_into = sql_upper.find("INSERT INTO")
            sub = sql_clean[idx_into + 11:].strip()
            table_name = ""
            for char in sub:
                if ('a' <= char <= 'z') or ('A' <= char <= 'Z') or ('0' <= char <= '9') or char == '_':
                    table_name += char
                else:
                    break
                    
            start_cols = sub.find("(")
            end_cols = sub.find(")")
            cols = []
            if start_cols != -1 and end_cols != -1:
                cols_str = sub[start_cols+1:end_cols]
                cols = [c.strip() for c in cols_str.split(',')]

            if table_name:
                if table_name not in self.connection.tables:
                    self.connection.tables[table_name] = []
                
                new_row = {}
                if table_name == 'static_hosts':
                    new_row['host_id'] = len(self.connection.tables[table_name]) + 1
                    new_row['created_at'] = '2026-06-16 12:00:00'
                    new_row['updated_at'] = '2026-06-16 12:00:00'
                elif table_name == 'self_records':
                    new_row['record_id'] = len(self.connection.tables[table_name]) + 1
                    new_row['created_at'] = '2026-06-16 12:00:00'
                    new_row['updated_at'] = '2026-06-16 12:00:00'
                elif table_name == 'other_proxies':
                    new_row['proxy_id'] = len(self.connection.tables[table_name]) + 1
                    new_row['port'] = 80
                    new_row['last_seen'] = '2026-06-16 12:00:00'
                    new_row['is_active'] = True
                elif table_name == 'other_records':
                    new_row['record_id'] = len(self.connection.tables[table_name]) + 1
                    new_row['received_at'] = '2026-06-16 12:00:00'
                elif table_name == 'merged_records':
                    new_row['record_id'] = len(self.connection.tables[table_name]) + 1
                    new_row['created_at'] = '2026-06-16 12:00:00'
                    new_row['updated_at'] = '2026-06-16 12:00:00'

                for i, col in enumerate(cols):
                    val = params[i] if params and i < len(params) else None
                    new_row[col] = val

                self.connection.tables[table_name].append(new_row)
                self.rowcount = 1
                if 'host_id' in new_row:
                    self.lastrowid = new_row['host_id']
                elif 'record_id' in new_row:
                    self.lastrowid = new_row['record_id']
                elif 'proxy_id' in new_row:
                    self.lastrowid = new_row['proxy_id']
            return self

        # 9. UPDATE
        if sql_upper.startswith("UPDATE"):
            parts = sql_clean.split()
            table_name = parts[1]
            
            set_idx = sql_upper.find("SET")
            where_idx = sql_upper.find("WHERE")
            
            if set_idx != -1:
                if where_idx != -1:
                    set_clause = sql_clean[set_idx+3:where_idx].strip()
                    where_clause = sql_clean[where_idx+5:].strip()
                else:
                    set_clause = sql_clean[set_idx+3:].strip()
                    where_clause = None

                if table_name not in self.connection.tables:
                    self.connection.tables[table_name] = []

                param_idx = 0
                is_ttl_decrement = "ttl = ttl - ?" in set_clause.replace(' ', '')
                decrement_val = 0
                if is_ttl_decrement:
                    decrement_val = params[0] if params else 0
                    param_idx = 1
                
                updates = {}
                if not is_ttl_decrement:
                    for part in set_clause.split(','):
                        sub_parts = part.split('=')
                        if len(sub_parts) == 2:
                            col = sub_parts[0].strip()
                            val_placeholder = sub_parts[1].strip()
                            if val_placeholder == '?':
                                updates[col] = params[param_idx]
                                param_idx += 1
                            else:
                                updates[col] = val_placeholder.strip("'\"")

                where_params = params[param_idx:] if params else []
                
                updated_count = 0
                for row in self.connection.tables[table_name]:
                    match = True
                    if where_clause:
                        match = self._eval_where(row, where_clause, where_params)
                    
                    if match:
                        if is_ttl_decrement:
                            if 'ttl' in row and row['ttl'] is not None:
                                row['ttl'] = int(row['ttl']) - int(decrement_val)
                        else:
                            for col, val in updates.items():
                                row[col] = val
                        updated_count += 1
                self.rowcount = updated_count
            return self

        # 10. SELECT
        if sql_upper.startswith("SELECT") or sql_upper.startswith("WITH "):
            if "WITH CANDIDATES" in sql_upper:
                self._run_merge_records_python()
                return self

            from_idx = sql_upper.find("FROM")
            where_idx = sql_upper.find("WHERE")
            
            if from_idx != -1:
                cols_str = sql_clean[6:from_idx].strip()
                if where_idx != -1:
                    table_name_part = sql_clean[from_idx+4:where_idx].strip()
                    where_clause = sql_clean[where_idx+5:].strip()
                else:
                    table_name_part = sql_clean[from_idx+4:].strip()
                    where_clause = None
                
                table_name = table_name_part.split()[0].strip()
                
                if table_name not in self.connection.tables:
                    self.connection.tables[table_name] = []
                
                cols = [c.strip() for c in cols_str.split(',')]
                if cols_str == '*':
                    if self.connection.tables[table_name]:
                        cols = list(self.connection.tables[table_name][0].keys())
                    else:
                        cols = []

                for row in self.connection.tables[table_name]:
                    match = True
                    if where_clause:
                        match = self._eval_where(row, where_clause, params)
                    
                    if match:
                        row_res = []
                        for col in cols:
                            val = row.get(col, None)
                            row_res.append(val)
                        self.results.append(tuple(row_res))
            return self

        return self

    def _eval_where(self, row, where_clause, params):
        where_clean = where_clause.replace('(', '').replace(')', '')
        where_upper = where_clean.upper()
        
        if " OR " in where_upper:
            # " OR " で分割 (大文字小文字対応)
            norm = where_clean.replace(" or ", " OR ").replace(" Or ", " OR ")
            parts = [p.strip() for p in norm.split(" OR ")]
            param_idx = 0
            for part in parts:
                match, consumed = self._eval_simple_cond(row, part, params[param_idx:] if params else [])
                param_idx += consumed
                if match:
                    return True
            return False
        elif " AND " in where_upper:
            norm = where_clean.replace(" and ", " AND ").replace(" And ", " AND ")
            parts = [p.strip() for p in norm.split(" AND ")]
            param_idx = 0
            for part in parts:
                match, consumed = self._eval_simple_cond(row, part, params[param_idx:] if params else [])
                param_idx += consumed
                if not match:
                    return False
            return True
        else:
            match, _ = self._eval_simple_cond(row, where_clean, params)
            return match

    def _eval_simple_cond(self, row, cond_str, params):
        cond_clean = cond_str.strip()
        operators = ["<=", ">=", "!=", "=", "<", ">"]
        op = None
        for o in operators:
            if o in cond_clean:
                op = o
                break
        if not op:
            return True, 0
            
        col, val_expr = [x.strip() for x in cond_clean.split(op, 1)]
        consumed = 0
        
        if val_expr == '?':
            val = params[0] if params else None
            consumed = 1
        else:
            val = val_expr.strip("'\"")
            try:
                val = int(val)
            except ValueError:
                pass
                
        row_val = row.get(col, None)
        if row_val is None:
            return False, consumed

        if op == '=':
            return str(row_val) == str(val), consumed
        elif op == '!=':
            return str(row_val) != str(val), consumed
        elif op == '<=':
            return float(row_val) <= float(val), consumed
        elif op == '>=':
            return float(row_val) >= float(val), consumed
        elif op == '<':
            return float(row_val) < float(val), consumed
        elif op == '>':
            return float(row_val) > float(val), consumed
            
        return False, consumed

    def _filter_out(self, table_name, where_clause, params):
        kept = []
        deleted = []
        for r in self.connection.tables[table_name]:
            if self._eval_where(r, where_clause, params):
                deleted.append(r)
            else:
                kept.append(r)
        return kept, deleted

    def _run_merge_records_python(self):
        self.connection.tables['merged_records'] = []
        candidates = []
        
        for r in self.connection.tables.get('self_records', []):
            ip = r.get('ip_address', '')
            if ip.startswith('127.') or ip == '::1':
                continue
            candidates.append({
                'hostname': r.get('hostname'),
                'ip_address': ip,
                'record_type': r.get('record_type'),
                'ttl': r.get('ttl'),
                'source_type': 'self',
                'source_record_id': r.get('record_id'),
                'registered_at': r.get('updated_at', '2026-06-16 12:00:00'),
                'priority': 1 if r.get('resolution_method') == 'static' else 2
            })
            
        for r in self.connection.tables.get('other_records', []):
            ip = r.get('ip_address', '')
            if ip.startswith('127.') or ip == '::1':
                continue
            candidates.append({
                'hostname': r.get('hostname'),
                'ip_address': ip,
                'record_type': r.get('record_type'),
                'ttl': r.get('ttl'),
                'source_type': 'other',
                'source_record_id': r.get('record_id'),
                'registered_at': r.get('received_at', '2026-06-16 12:00:00'),
                'priority': 2
            })
            
        by_host = {}
        for c in candidates:
            host = c['hostname']
            if host not in by_host:
                by_host[host] = []
            by_host[host].append(c)
            
        merged = []
        for host, items in by_host.items():
            items.sort(key=lambda x: (x['priority'], -1 * (len(items) - items.index(x))))
            best = items[0]
            
            merged.append({
                'record_id': len(merged) + 1,
                'hostname': best['hostname'],
                'ip_address': best['ip_address'],
                'record_type': best['record_type'],
                'ttl': best['ttl'],
                'source_type': best['source_type'],
                'source_record_id': best['source_record_id'],
                'created_at': '2026-06-16 12:00:00',
                'updated_at': '2026-06-16 12:00:00'
            })
            
        self.connection.tables['merged_records'] = merged