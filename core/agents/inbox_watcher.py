import os
import json
import time
import sys
import argparse
from pathlib import Path

class InboxWatcher:
    def __init__(self, inbox_path):
        self.inbox_path = Path(inbox_path) / 'alerts_inbox.jsonl'
        self.session_dir = Path('/home/rohit/.hermes/sessions')

    def inject_to_active_sessions(self, message):
        """Inject the alert into all active session files as a [SYSTEM:] message."""
        if not self.session_dir.exists(): return
        for session_file in self.session_dir.glob('session_*.json'):
            try:
                # Check if file was modified in last 60 minutes
                if time.time() - session_file.stat().st_mtime > 3600:
                    continue
                    
                with open(session_file, 'r') as f:
                    data = json.load(f)
                
                # Append the system alert
                if 'messages' not in data: data['messages'] = []
                data['messages'].append({
                    'role': 'user', 
                    'content': f'⚕ [SYSTEM: CRITICAL ALERT: {message}. Please delegate this to General/Infra if out of scope and resume your current mission.]'
                })
                
                with open(session_file, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f'Injected alert into {session_file.name}')
            except Exception as e:
                print(f'Error injecting to {session_file.name}: {e}')

    def check_alerts(self):
        if not self.inbox_path.exists(): return
        
        try:
            lines = self.inbox_path.read_text().splitlines()
        except Exception:
            return

        new_lines = []
        injected_count = 0
        
        for line in lines:
            if not line.strip(): continue
            try:
                alert = json.loads(line)
            except Exception:
                continue

            if alert.get('injected'):
                new_lines.append(line)
                continue
                
            msg = alert.get('message', 'Unknown Alert')
            
            # Inject to active agent sessions if critical
            if 'CRITICAL' in msg.upper() or 'FATAL' in msg.upper():
                self.inject_to_active_sessions(msg)
                alert['injected'] = True
                injected_count += 1
                
            new_lines.append(json.dumps(alert))

        if injected_count > 0:
            try:
                self.inbox_path.write_text('\n'.join(new_lines) + '\n')
            except Exception:
                pass

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inbox-dir', default='/home/rohit/agentharness/data')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()

    watcher = InboxWatcher(args.inbox_dir)
    
    if args.once:
        watcher.check_alerts()
    else:
        print("Running in daemon mode...")
        while True:
            watcher.check_alerts()
            time.sleep(5)
