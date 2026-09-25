# Mohra App - Readora Automation Tool

Automated tool designed for **Windows** (and macOS/Linux) to process stories assigned to **Mohra**, extract **Comprehension Questions** from Google Drive docx files, and automatically populate them into **Readora Lab** (`https://www.readoralab.com`).

---

## 🌟 Key Features

1. **Always-On Background Service & Auto-Startup**:
   - Double-click `install_startup.bat` to configure the app to **run silently in the background forever** and **launch automatically every time Windows boots**.
   - Sits quietly in the **Windows System Tray** (near the clock).
   - Auto-recovers from network drops and unexpected errors so it **never closes or crashes**.
2. **Auto-Detects Downloaded Sheet**:
   - Automatically detects when a new or updated Google Sheet (`Data Entry _ Website _ First Language.xlsx`) arrives in your `Downloads` folder!
   - Can also download fresh updates directly via Google Sheets URL.
3. **Universal Google Drive Docx Downloader**:
   - Downloads docx files (`First language.docx`, `Second Language.docx`, `New.docx`, etc.) directly in seconds.
   - Automatically extracts file IDs without manual clicking.
4. **Intelligent Comprehension Question Parser**:
   - Supports **both question formats**:
     - Grade 1 style: `Where does the bear sit? A. ... B. ... Answer: B`
     - Grade 4 style: `Q1. What are props? A. ... B. (Correct answer) ...`
   - Extracts all 10 questions, 4 choices, and exact correct answer cleanly.
5. **Readora Lab Web Automation**:
   - Logs in with credentials (`super9@test.com` / `12345678`).
   - Searches for the story book in `/super_admin/books`.
   - Opens the book edit page and clicks **Edit Questions**.
   - Sets Question Header to `"Choose the correct answer "`.
   - Sets Content Type to `"None"`.
   - Clears any previous questions.
   - Inserts all 10 questions with their 4 choices, marks correct answer checkbox, and enables **Auto Corrected** switch.
6. **Interactive Dry-Run Review & "Accept & Apply"**:
   - Preview exactly what will be changed before committing to Readora Lab.
   - Shows Story Title, Grade, parsed docx questions, choices (A, B, C, D), and correct answer key.
   - Inspect and edit question rubric text or choices directly inside the Review dialog.
   - Click **`🚀 Accept & Apply to Readora`** to execute the live update with your reviewed questions immediately!
   - Available via:
     - **GUI**: Action button **`🔍 Review & Dry-Run`**, double-clicking any story row, or right-click context menu.
     - **CLI**: `python mohra_app.py --review "Story Name"` or menu option 4.
7. **Silent Auto-Update System**:
   - Automatically checks for code and dependency updates in the background (default: every 1 hour).
   - Silently pulls updates (via Git or remote URL), installs updated dependencies, and performs an instant hot-restart with zero interruption.
   - Preserves all your local settings (`config.json`) and progress (`progress.json`).
8. **Threading as a Core Architectural Principle**:
   - Built on a dedicated concurrency engine (`ThreadManager`, `BaseWorker`, `PeriodicWorker`, `CallableWorker`).
   - Every module runs asynchronously in managed background threads with live progress reporting, cancellation checkpoints, and thread safety.
   - Any new module added in the future can run in the background in less than 20 lines of code.
9. **Multiple Interfaces**:
   - **System Tray & Background Daemon**: Sits in background, monitors sheets, processes automatically.
   - **Desktop Window (GUI)**: Double-click `run_gui.bat` with live background progress bar and cancellation.
   - **Command Line (CLI)**: Double-click `run.bat`.

---

## 🧵 Threading Architecture (Core Principle for Any Module)

Mohra treats **threading and background execution as a first-class citizen**. All I/O, sheet watching, document downloading, updater checks, and browser automation execute inside isolated worker threads managed by `ThreadManager`.

### Adding ANY New Module to the Background:
To add any new module to run in the background, choose one of 3 simple patterns:
- **Pattern 1 (`BaseWorker`)**: Subclass `BaseWorker` and implement `work(self)` with `self.report_progress(...)` and `self.check_cancellation()`.
- **Pattern 2 (`PeriodicWorker`)**: Subclass `PeriodicWorker` for recurring monitors, pollers, or syncers.
- **Pattern 3 (`@run_in_background`)**: Decorate any function to turn it into an async background task.

See the complete guide in [docs/THREADING_GUIDE.md](file:///Users/kirolos/Desktop/Projects/mohra/docs/THREADING_GUIDE.md) and working example in [modules/example_module.py](file:///Users/kirolos/Desktop/Projects/mohra/modules/example_module.py).

---

## 🚀 Quick Setup for Windows

### Step 1: Run Setup (First Time Only)
Double-click:
```
setup.bat
```
This automatically:
- Creates a Python virtual environment (`venv`)
- Installs all dependencies (`playwright`, `python-docx`, `openpyxl`, `rich`, `pystray`, `Pillow`)
- Installs the Chromium browser engine for automation

---

## 🔄 Always-On Background Mode (Open on Startup)

### To Run in Background & Start on Boot:
Double-click:
```
install_startup.bat
```
- This adds a silent startup launcher to your Windows Startup folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MohraAutomation.vbs`).
- It starts the service immediately without any black command prompt window!
- The app will now **always run in the background** and **automatically start whenever your computer turns on**.

### System Tray Controls:
Look for the **Mohra icon** in the bottom-right corner of your screen (near the Windows clock):
- Right-click the icon to:
  * 🔄 **Check & Process Now**: Manually trigger processing of any pending stories.
  * 🖥️ **Open GUI Dashboard**: Open the visual management window.
  * 📝 **View Activity Logs**: Open `logs/background.log` to see real-time history.
  * ⚙️ **Toggle Dry-Run Mode**: Switch between Safe Dry-Run and Live execution.
  * ❌ **Exit**: Stop the background service.

### To Stop or Remove from Startup:
- To stop the background service: Double-click `stop_background.bat`
- To remove from Windows startup: Double-click `uninstall_startup.bat`

---

## ⚡ Silent Auto-Updates

The app includes a fully automated, silent update engine:

### How it Works:
1. **Periodic Background Check**:
   - The background worker checks for updates automatically (default: every 1 hour).
   - If updates are found in your Git repository or remote URL, it downloads and applies them **completely silently without showing any black CMD window**.
2. **Dependency Sync**:
   - If `requirements.txt` was updated with new packages, it silently installs them via `pip`.
3. **Hot-Restart**:
   - The app restarts itself in the background seamlessly so the latest improvements take effect immediately.
4. **Configuration Protection**:
   - Your local `config.json` (passwords, preferences) and `progress.json` are **never overwritten** during an update.

### Linking Your GitHub Repository (One-time):
To connect the app to your GitHub repository so it pulls your updates:
```bash
git remote add origin <YOUR_GITHUB_REPO_URL>
git push -u origin main
```
From that moment on:
- Whenever you push new code to GitHub (`git push`), the app on the Windows PC will **automatically pull the update silently**, sync dependencies, and restart itself in the background!
- Your local credentials in `config.json` and tracking state in `progress.json` are in `.gitignore` and **will never be touched or cause merge conflicts**.

### Manual Update Triggers:
- **From System Tray**: Right-click the Mohra icon near your clock and click **⚡ Check for Updates**.
- **From Batch Script**: Double-click `update.bat`.

---

## 🖥️ Other Launch Options

- **Desktop Window (GUI)**: Double-click `run_gui.bat`
- **Command Line (CLI)**: Double-click `run.bat`
- **Silent Background Run (One-time)**: Double-click `start_background.bat`

---

## 📁 Project Structure

```
mohra/
├── install_startup.bat    # 1-Click: Enable auto-start on boot & run in background
├── uninstall_startup.bat  # 1-Click: Remove from auto-start & stop background
├── start_background.bat   # 1-Click: Start silent background service immediately
├── stop_background.bat    # 1-Click: Stop running background processes
├── silent_start.vbs       # Windows VBScript for 100% hidden background execution
├── background_service.py  # Resilient background daemon with crash recovery
├── tray_app.py            # Windows System Tray application
├── mohra_app.py           # Interactive CLI application
├── gui.py                 # Desktop Graphical Interface (Tkinter)
├── config.py & config.json# Settings (credentials, URLs, modes, intervals)
├── setup.bat              # 1-Click setup script for Windows
├── update.bat             # 1-Click update checker for Windows
├── run.bat                # 1-Click CLI launcher for Windows
├── run_gui.bat            # 1-Click GUI launcher for Windows
├── requirements.txt       # Dependencies
├── docs/
│   └── THREADING_GUIDE.md # Comprehensive guide for background worker architecture
├── modules/
│   ├── threading_manager.py# Central ThreadManager, BaseWorker, PeriodicWorker & event bus
│   ├── workers.py         # Standardized background workers (Story, Sheet, Updater, Chrome)
│   ├── example_module.py  # Reference template for creating ANY new background module
│   ├── updater.py         # Silent auto-updater engine
│   ├── sheet_parser.py    # Auto-detects Excel sheet in Downloads or online
│   ├── drive_downloader.py# Downloads docx files directly from Google Drive
│   ├── docx_parser.py     # Parses Comprehension Questions & answers
│   ├── readora_client.py  # Playwright web automation for Readora Lab
│   ├── progress.py        # Tracks progress in progress.json (thread-safe)
│   └── chrome_launcher.py # Chrome profile launcher for Gmail
├── logs/                  # Background service and updater log files
└── cache/                 # Local cache for downloaded docx files
```

---

## ⚙️ Configuration (`config.json`)

```json
{
  "sheet_url": "https://docs.google.com/spreadsheets/d/14Nwv3_w83pvpAE7SqjDi2rMoQezuiCtJ0YCLC_w_2gk/edit?pli=1&gid=0#gid=0",
  "sheet_source": "url",
  "assigned_to": "Mohra",
  "gmail_account": "mohrawagdy58@gmail.com",
  "readora_login_url": "https://www.readoralab.com/auth/login",
  "readora_books_url": "https://www.readoralab.com/super_admin/books",
  "readora_email": "super9@test.com",
  "readora_password": "12345678",
  "question_header": "Choose the correct answer ",
  "preferred_language_file": "First language",
  "content_type": "None",
  "dry_run": true,
  "headless": true,
  "background_check_interval_seconds": 300,
  "watch_downloads_folder": true,
  "auto_process_pending": true
}
```

---

## 📦 Building Windows Package (.exe) & Installer

You can package Mohra into a standalone Windows executable (`Mohra.exe`) or a full Windows setup wizard installer (`Mohra_Setup.exe`):

### Option 1: Automatic Cloud Build (Recommended for macOS / CI)
Since you are developing on macOS or Linux, Windows executables can be built automatically in GitHub's cloud:
1. Push your code to GitHub (`git push origin main`).
2. Go to the **Actions** tab on your GitHub repository (`Kirolos-Nabil-0/mohra`).
3. Select **Build Windows Package (.exe)** and click **Run workflow** (or wait for the automatic push build).
4. Download the artifacts:
   - `Mohra-Setup-Installer` (`Mohra_Setup_v1.0.0.exe`) - Full Windows setup wizard with Desktop & Start Menu shortcuts.
   - `Mohra-Standalone-Windows` (`Mohra-Windows.zip`) - Portable folder with `Mohra.exe`.

### Option 2: Local One-Click Build on Windows
On any Windows PC:
1. Double-click **`build_exe.bat`**.
2. The script will automatically:
   - Set up the virtual environment and install PyInstaller and all dependencies.
   - Install Playwright's Chromium browser engine.
   - Compile `Mohra.exe` using `mohra.spec`.
   - Prepare necessary directories (`logs/`, `cache/`, `chrome_profile/`) and configuration.
   - Compress the output into `dist\Mohra-Windows.zip`.
3. To generate an installer wizard (`Mohra_Setup.exe`):
   - Install [Inno Setup](https://jrsoftware.org/isdl.php).
   - Right-click `installer.iss` and click **Compile**, or run:
     ```cmd
     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
     ```
   - The installer will be saved to `dist\Installer\Mohra_Setup_v1.0.0.exe`.

