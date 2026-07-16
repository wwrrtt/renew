import json
import os
import base64

DIR = os.path.dirname(os.path.abspath(__file__))
storage_path = os.path.join(DIR, "storage_state.json")

if not os.path.exists(storage_path):
    print("❌ 找不到 storage_state.json，请先运行 save_storage.py")
    exit(1)

with open(storage_path, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

print("=" * 60)
print("Copy the following to GitHub Secrets -> STORAGE_STATE_B64:")
print("=" * 60)
print(b64)
print("=" * 60)
