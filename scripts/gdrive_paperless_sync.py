import os, json, logging, io; from google.oauth2.credentials import Credentials; from googleapiclient.discovery import build; from googleapiclient.http import MediaIoBaseDownload
TOKEN_PATH = os.path.expanduser("~/.hermes/google_token.json"); SYNC_DB = os.path.expanduser("~/.hermes/gdrive_sync_db.json"); SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Target Folders
BASE_DIR = "/mnt/usb/files/paperless-consume/"
PERSONAL_DIR = os.path.join(BASE_DIR, "personal/")
BUDDHISM_DIR = os.path.join(BASE_DIR, "ebooks/buddhism/")
CAREER_DIR = os.path.join(BASE_DIR, "ebooks/career/")
IDS_DIR = os.path.join(BASE_DIR, "ids/")

# Intelligence Sets
BUDDHISM_KWS = ['buddhism', 'buddha', 'zen', 'dharma', 'sutra', 'meditation', 'orissa', 'goddess', 'india', 'sculpture', 'tirtha', 'puratatva', 'nibandhavali', 'senapati', 'darurupa']
CAREER_KWS = ["interview", "pm ", "career", "amazon", "product manager", "handbook", "skills", "pmo", "tpm"]
IDS_KWS = ["passport", "green card", "uscis", "resident", "id card", "i-551", "i-485", "visa"]

logging.basicConfig(level=logging.INFO); log = logging.getLogger("gdrive_sync")
def sync():
    for d in [PERSONAL_DIR, BUDDHISM_DIR, CAREER_DIR, IDS_DIR]: os.makedirs(d, exist_ok=True)
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    db = json.load(open(SYNC_DB)) if os.path.exists(SYNC_DB) else {"synced_ids": []}
    
    # Updated query to include images
    query = "(mimeType='application/pdf' or mimeType='image/jpeg' or mimeType='image/png') and trashed=false"
    items = service.files().list(q=query, fields="files(id, name, mimeType)").execute().get('files', [])
    
    synced_count = 0
    for item in items:
        if item['id'] in db["synced_ids"]: continue
        
        name_lower = item['name'].lower()
        
        # Priority 1: ID Cards
        if any(kw in name_lower for kw in IDS_KWS):
            target_dir = IDS_DIR
        # Priority 2: Buddhism
        elif any(kw in name_lower for kw in BUDDHISM_KWS):
            target_dir = BUDDHISM_DIR
        # Priority 3: Career
        elif any(kw in name_lower for kw in CAREER_KWS):
            target_dir = CAREER_DIR
        # Priority 4: PDFs that are none of the above (Personal)
        elif item['mimeType'] == 'application/pdf':
            target_dir = PERSONAL_DIR
        else:
            # Skip images that don't match any ID keywords
            continue
            
        log.info(f"Syncing to {os.path.basename(os.path.normpath(target_dir))}: {item['name']}")
        try:
            with io.FileIO(os.path.join(target_dir, item['name']), 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, service.files().get_media(fileId=item['id']))
                done = False
                while not done: status, done = downloader.next_chunk()
            db["synced_ids"].append(item['id'])
            synced_count += 1
        except Exception as e:
            log.error(f"Failed to sync {item['name']}: {e}")
            
    json.dump(db, open(SYNC_DB, 'w'))
    log.info(f"Sync finished. {synced_count} files added.")

if __name__ == "__main__":
    try: sync()
    except Exception as e: log.error(f"Error: {e}")
