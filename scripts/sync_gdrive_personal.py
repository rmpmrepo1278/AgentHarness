#!/usr/bin/env python3
"""
Sync Google Drive PDFs to Paperless with auto-tagging.
Personal docs go to /personal, ebooks go to /ebooks.
"""
import os
import sys
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PERSONAL_DIR = Path("/mnt/usb/files/paperless-consume/personal")
EBOOKS_DIR = Path("/mnt/usb/files/paperless-consume/ebooks")

# Clear personal indicators - these are definitely personal docs
PERSONAL_PATTERNS = [
    'tax', 'invoice', 'receipt', 'w-2', 'w2', '1099', '1095c', '5498',
    'statement', 'insurance', 'license', 'passport', 'social', 'password',
    'contract', 'agreement', 'bill', 'payment', 'form', 'application',
    'resume', 'cover letter', 'medical', 'health', 'financial', 'bank',
    'investment', 'loan', 'mortgage', 'deed', 'title', 'registration',
    'dmv', 'utility', 'phone', 'cable', 'conf. ', 'confirmation',
    'menu', 'reservation', 'ticket', 'boarding', 'itinerary'
]

def is_personal_doc(filename):
    lower = filename.lower()
    # Check for personal patterns
    for kw in PERSONAL_PATTERNS:
        if kw in lower:
            return True
    # Year + doc type patterns
    import re
    if re.search(r'20\d{2}.*(tax|invoice|receipt|statement|form|return)', lower):
        return True
    return False

def download_file(file_info):
    name, file_id = file_info
    if is_personal_doc(name):
        dest_dir = PERSONAL_DIR
        tag = "personal"
    else:
        dest_dir = EBOOKS_DIR
        tag = "ebooks"
    
    dest_path = dest_dir / name
    if dest_path.exists():
        return f"skip: {name}"
    
    url = f"https://drive.google.com/uc?id={file_id}"
    try:
        result = subprocess.run(['curl', '-sL', '-m', '30', url, '-o', str(dest_path)], 
                                capture_output=True, timeout=60)
        return f"synced: {name} -> {tag}"
    except Exception as e:
        return f"error: {name}: {e}"

def main():
    result = subprocess.run(
        ['python3', '/home/rohit/.hermes/hermes-agent/skills/productivity/google-workspace/scripts/google_api.py',
         'drive', 'search', '--raw-query', "mimeType='application/pdf'", '--max', '1000'],
        capture_output=True, text=True, timeout=120
    )
    
    files = json.loads(result.stdout)
    to_download = [(f['name'], f['id']) for f in files]
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(download_file, to_download))
    
    synced = [r for r in results if r.startswith('synced')]
    skipped = [r for r in results if r.startswith('skip')]
    print(f"Synced: {len(synced)}, Skipped: {len(skipped)}")
    for r in synced[:5]:
        print(r)

if __name__ == "__main__":
    main()
