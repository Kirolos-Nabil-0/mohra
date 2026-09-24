import sys
import os
import threading
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

from config import load_config, save_config
from background_service import BackgroundService

def create_tray_image():
    # Generate a clean 64x64 icon with a book / letter 'M' icon
    width = 64
    height = 64
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    dc = ImageDraw.Draw(image)
    # Circle background (Cyan / Deep blue)
    dc.ellipse([4, 4, 60, 60], fill=(0, 168, 204, 255), outline=(255, 255, 255, 255), width=2)
    # Draw an 'M' in the center
    # M shape
    points = [(16, 44), (16, 20), (32, 34), (48, 20), (48, 44)]
    dc.line(points, fill=(255, 255, 255, 255), width=4)
    return image

class TrayApplication:
    def __init__(self):
        self.service = BackgroundService()
        self.worker_thread = None
        self.icon = None

    def start(self):
        # Start background worker loop in separate daemon thread
        self.worker_thread = threading.Thread(target=self.service.run_forever, daemon=True)
        self.worker_thread.start()

        try:
            import pystray
            from pystray import MenuItem as item, Menu

            cfg = load_config()
            mode_text = "Dry-Run: ON (Safe)" if cfg.get("dry_run", True) else "Live Mode: ON"

            def on_process_now(icon, item):
                threading.Thread(target=self.service.process_pending_stories, daemon=True).start()

            def on_open_gui(icon, item):
                # Launch GUI in separate process
                python_exe = sys.executable
                gui_path = str(Path(__file__).parent / "gui.py")
                subprocess.Popen([python_exe, gui_path])

            def on_open_log(icon, item):
                log_path = Path("./logs/background.log").resolve()
                if sys.platform == "win32":
                    os.startfile(str(log_path))
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(log_path)])
                else:
                    subprocess.Popen(["xdg-open", str(log_path)])

            def on_toggle_dry_run(icon, item):
                cfg = load_config()
                cfg["dry_run"] = not cfg.get("dry_run", True)
                save_config(cfg)
                self.service.config = cfg

            def on_check_update(icon, item):
                def update_worker():
                    self.service.updater.config = load_config()
                    self.service.updater.check_and_apply_update_silently()
                threading.Thread(target=update_worker, daemon=True).start()

            def on_exit(icon, item):
                self.service.stop()
                icon.stop()

            menu = Menu(
                item("Mohra Automation (Running)", lambda icon, item: None, enabled=False),
                item("🔄 Check & Process Now", on_process_now),
                item("⚡ Check for Updates", on_check_update),
                item("🖥️ Open GUI Dashboard", on_open_gui),
                item("📝 View Activity Logs", on_open_log),
                item("⚙️ Toggle Dry-Run Mode", on_toggle_dry_run),
                item("❌ Exit", on_exit)
            )

            self.icon = pystray.Icon("MohraApp", create_tray_image(), "Mohra App Automation (Active)", menu)
            self.icon.run()

        except Exception as e:
            print(f"[Tray] Pystray/GUI not available ({e}). Running in pure console background mode.")
            # Keep main thread alive
            try:
                while self.service.running:
                    self.worker_thread.join(timeout=1.0)
            except KeyboardInterrupt:
                self.service.stop()

def main():
    app = TrayApplication()
    app.start()

if __name__ == "__main__":
    main()
