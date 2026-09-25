import sys
import os
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

from config import load_config, save_config
from background_service import BackgroundService
from modules.threading_manager import ThreadManager

def create_tray_image():
    # Generate a clean 64x64 icon with a book / letter 'M' icon
    width = 64
    height = 64
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    dc = ImageDraw.Draw(image)
    # Circle background (Cyan / Deep blue)
    dc.ellipse([4, 4, 60, 60], fill=(0, 168, 204, 255), outline=(255, 255, 255, 255), width=2)
    # Draw an 'M' in the center
    points = [(16, 44), (16, 20), (32, 34), (48, 20), (48, 44)]
    dc.line(points, fill=(255, 255, 255, 255), width=4)
    return image

class TrayApplication:
    def __init__(self):
        self.manager = ThreadManager.get_instance()
        self.service = BackgroundService()
        self.icon = None

    def start(self):
        # Start background workers via the BackgroundService
        self.service.start()

        try:
            import pystray
            from pystray import MenuItem as item, Menu

            def on_process_now(icon, item):
                self.service.process_pending_stories(blocking=False)

            def on_run_key(icon, item):
                self.service.run_key_module(blocking=False)

            def on_stop_tasks(icon, item):
                active = self.manager.list_active()
                for w in active:
                    if "Automation" in w.name or "Key" in w.name:
                        w.stop()

            def on_open_gui(icon, item):
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
                    from modules.updater import AutoUpdater
                    updater = AutoUpdater(load_config())
                    updater.check_and_apply_update_silently()
                self.manager.submit_task(update_worker, name="ManualUpdateCheckTask")

            def on_exit(icon, item):
                self.service.stop()
                icon.stop()

            menu = Menu(
                item("Mohra Automation (Active)", lambda icon, item: None, enabled=False),
                item("🔄 Process Pending Stories Now", on_process_now),
                item("🔑 Run 'key' Module Now", on_run_key),
                item("⏹ Stop Current Automation", on_stop_tasks),
                item("⚡ Check for Updates", on_check_update),
                item("🖥️ Open GUI Dashboard", on_open_gui),
                item("📝 View Activity Logs", on_open_log),
                item("⚙️ Toggle Dry-Run Mode", on_toggle_dry_run),
                item("❌ Exit", on_exit)
            )

            self.icon = pystray.Icon("MohraApp", create_tray_image(), "Mohra App Automation", menu)
            self.icon.run()

        except Exception as e:
            print(f"[Tray] Pystray/GUI not available ({e}). Running in pure console background mode.")
            try:
                while self.service.running:
                    import time
                    time.sleep(1.0)
            except KeyboardInterrupt:
                self.service.stop()

def main():
    app = TrayApplication()
    app.start()

if __name__ == "__main__":
    main()
