#!/usr/bin/env python3
"""Query audit_store.db structure for L10K baseline definition"""

import sqlite3
import sys

db_path = r'D:\LiuHao-AI-OS\audit_store.db'

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get all table names
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    
    print("=== audit_store.db Structure ===\n")
    print(f"Found {len(tables)} table(s):\n")
    
    for table in tables:
        table_name = table[0]
        print(f"Table: {table_name}")
        
        # Get table schema
        cursor.execute(f"PRAGMA table_info({table_name});")
        columns = cursor.fetchall()
        
        print("  Columns:")
        for col in columns:
            print(f"    - {col[1]} ({col[2]})")
        
        # Get row count
        cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
        count = cursor.fetchone()[0]
        print(f"  Row count: {count}\n")
        
        # Sample first 3 rows if available
        if count > 0:
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 3;")
            rows = cursor.fetchall()
            print(f"  Sample data (first {min(3, count)} rows):")
            for row in rows:
                print(f"    {row}")
            print()
    
    conn.close()
    print("Query completed successfully.")
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
