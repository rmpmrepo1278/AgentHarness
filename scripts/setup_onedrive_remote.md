# OneDrive Setup for Calibre Backup

Since the homelab server is headless, you need to set up the Microsoft OneDrive OAuth from a machine with a browser.

## Option 1: Run from your local machine (Recommended)

### Step 1: Install rclone on your local machine
- **Mac**: `brew install rclone`
- **Windows**: Download from https://rclone.org/downloads/
- **Linux**: `sudo apt install rclone`

### Step 2: Configure OneDrive remote
Run this command on your local machine:
```bash
rclone config create msonedrive onedrive
```

It will:
1. Open a browser window
2. Ask you to sign in to your Microsoft account (nickynrohit@live.com)
3. Grant permissions
4. Save the token automatically

### Step 3: Get the config
After authentication, rclone saves the config at:
- **Linux/Mac**: `~/.config/rclone/rclone.conf`
- **Windows**: `%APPDATA%\rclone\rclone.conf`

### Step 4: Copy the config to the homelab
Copy the entire `[msonedrive]` section from the rclone.conf file.

On the homelab server, the config file is at:
```
~/.config/rclone/rclone.conf
```

SSH into the homelab and paste the `[msonedrive]` section into the existing rclone.conf file.

### Step 5: Test the connection
```bash
rclone lsd msonedrive:/
```

You should see your OneDrive root folder.

### Step 6: Create the eBooks folder
```bash
rclone mkdir msonedrive:/Nicky/eBooks
```

## Option 2: Use SSH port forwarding

If you can SSH to the homelab with port forwarding:
```bash
ssh -L 53682:127.0.0.1:53682 rohit@192.168.29.10
```

Then run `rclone config create msonedrive onedrive` on the homelab — the browser will open on your local machine.

## After Setup

The daily sync will run automatically at 1:00 PM PT.

Manual sync:
```bash
bash /home/rohit/agentharness/scripts/sync_calibre_to_onedrive.sh
```

Check logs:
```bash
tail -f /home/rohit/agentharness/logs/calibre_onedrive_sync_*.log
```
