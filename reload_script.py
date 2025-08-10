# reload_script.py
import sys
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import subprocess

class RestartOnChangeHandler(FileSystemEventHandler):
    def __init__(self, script_path):
        self.script_path = script_path
        self.process = None
        self.restart()

    def restart(self):
        if self.process:
            self.process.terminate()
        print("Starting script...")
        self.process = subprocess.Popen([sys.executable, self.script_path])

    def on_any_event(self, event):
        if event.src_path.endswith('.py'):
            print(f"Detected change in {event.src_path}, restarting...")
            self.restart()

if __name__ == "__main__":
    path = "."  # โฟลเดอร์ที่จะดู
    script_to_run = "main.py"  # สคริปต์ที่ต้องการ run

    event_handler = RestartOnChangeHandler(script_to_run)
    observer = Observer()
    observer.schedule(event_handler, path, recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
