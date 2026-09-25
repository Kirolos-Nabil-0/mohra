import sys
import json
import argparse
from pathlib import Path
from typing import List, Dict, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from config import load_config, save_config
from modules.sheet_parser import SheetParser
from modules.drive_downloader import DriveDownloader
from modules.docx_parser import DocxParser
from modules.progress import ProgressTracker
from modules.chrome_launcher import launch_chrome_for_automation, find_chrome_executable
from modules.readora_client import ReadoraClient
from modules.threading_manager import ThreadManager, WorkerState
from modules.review_service import prepare_story_review, execute_accept_and_apply, fetch_story_readora_state

console = Console()

def display_banner(dry_run: bool):
    mode_text = "[bold yellow]DRY-RUN (Safe Mode - No DB Writes)[/bold yellow]" if dry_run else "[bold red]LIVE EXECUTION (Will Update Readora)[/bold red]"
    console.print(Panel.fit(
        f"[bold cyan]Mohra App Automation Tool[/bold cyan]\n"
        f"Google Drive → Docx Parser → Readora Lab Questions\n"
        f"Operating Mode: {mode_text}",
        border_style="cyan"
    ))

def list_stories(sheet_parser: SheetParser):
    with console.status("[bold green]Loading stories from sheet..."):
        stories = sheet_parser.parse_stories()

    table = Table(title=f"Stories Assigned to '{sheet_parser.assigned_to.title()}' (Total: {len(stories)})", show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", width=4)
    table.add_column("Grade/Sheet", width=12)
    table.add_column("Row", width=5)
    table.add_column("Story Title", style="cyan")
    table.add_column("Status / Comment", width=25)
    table.add_column("Drive Link", width=10)

    for i, s in enumerate(stories, 1):
        status_color = "green" if s["is_done"] else "yellow"
        has_link = "[green]Yes[/green]" if s["drive_url"] else "[red]No[/red]"
        table.add_row(
            str(i),
            s["sheet_name"],
            str(s["row_index"]),
            s["story_name"],
            f"[{status_color}]{s['comment'] or 'Pending'}[/{status_color}]",
            has_link
        )

    console.print(table)
    done_count = sum(1 for s in stories if s["is_done"])
    console.print(f"[bold]Summary:[/bold] [green]{done_count} Completed[/green] | [yellow]{len(stories) - done_count} Pending[/yellow]\n")

def process_single_story(story: Dict, config: dict, downloader: DriveDownloader, progress: ProgressTracker, client: Optional[ReadoraClient] = None) -> bool:
    story_name = story["story_name"]
    drive_url = story["drive_url"]
    dry_run = config.get("dry_run", True)

    console.print(f"\n[bold blue]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold blue]")
    console.print(f"[bold cyan]Processing Story:[/bold cyan] [bold white]{story_name}[/bold white] ({story.get('sheet_name', 'Sheet')} - Row {story.get('row_index', '?')})")

    if not drive_url:
        console.print(f"[red]Error:[/red] No Google Drive URL found for '{story_name}'")
        progress.record_failure(story, "No Drive URL")
        return False

    # 1. Download Docx
    preferred_lang = config.get("preferred_language_file", "First language")
    with console.status(f"[bold yellow]Downloading {preferred_lang}.docx from Google Drive..."):
        docx_path, status_msg = downloader.download_story_docx(drive_url, preferred_lang=preferred_lang)

    if not docx_path or not docx_path.exists():
        console.print(f"[red]Error:[/red] {status_msg}")
        progress.record_failure(story, status_msg)
        return False

    console.print(f"[green]✓[/green] Docx ready: [dim]{docx_path}[/dim] ({status_msg})")

    # 2. Parse Comprehension Questions
    questions = DocxParser.parse_comprehension_questions(docx_path)
    if not questions:
        console.print(f"[red]Error:[/red] No 'Comprehension Questions' could be parsed from {docx_path.name}")
        progress.record_failure(story, "No Comprehension Questions parsed")
        return False

    console.print(f"[green]✓[/green] Successfully parsed [bold]{len(questions)} Comprehension Questions[/bold]:")
    
    # Display preview table of parsed questions
    q_table = Table(show_header=True, header_style="bold blue")
    q_table.add_column("No.", width=4)
    q_table.add_column("Question", style="white")
    q_table.add_column("Choices (A, B, C, D)", style="dim")
    q_table.add_column("Ans", style="bold green", width=5)

    for q in questions:
        choices_str = " | ".join(f"{c['letter']}: {c['text'][3:]}" for c in q["choices"])
        q_table.add_row(str(q["num"]), q["raw_question"][:60], choices_str[:60] + "...", q["answer"])

    console.print(q_table)

    # 3. Readora Automation
    if client is None:
        console.print("[dim]No ReadoraClient active, skipping browser interaction.[/dim]")
        return True

    try:
        # Search book
        book_info = client.search_book(story_name)
        if not book_info:
            console.print(f"[red]Error:[/red] Book '{story_name}' not found on Readora Lab.")
            progress.record_failure(story, "Book not found on Readora")
            return False

        # Open edit
        client.open_book_edit(book_info)

        # Apply questions
        success = client.edit_questions(questions, dry_run=dry_run)
        if success:
            progress.record_success(story, len(questions), dry_run=dry_run)
            status_str = "[bold yellow]DRY-RUN SUCCESS[/bold yellow]" if dry_run else "[bold green]LIVE SUCCESS[/bold green]"
            console.print(f"{status_str}: Successfully processed '{story_name}'!\n")
            return True
        else:
            progress.record_failure(story, "Failed during question editing")
            return False

    except Exception as e:
        console.print(f"[red]Exception during Readora processing:[/red] {e}")
        progress.record_failure(story, str(e))
        return False


def review_and_apply_story_cli(story: Dict, config: dict) -> bool:
    """
    Performs an interactive Dry-Run review in the CLI:
    Shows all parsed questions, choices, and answers in a rich table,
    then prompts the user to Accept & Apply directly to Readora.
    """
    story_name = story["story_name"]
    console.print(f"\n[bold blue]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold blue]")
    console.print(f"[bold cyan]🔍 Dry-Run Review Inspector:[/bold cyan] [bold white]{story_name}[/bold white] ({story.get('sheet_name', 'Sheet')} - Row {story.get('row_index', '?')})")

    with console.status(f"[bold yellow]Preparing Dry-Run Review for '{story_name}'..."):
        res = prepare_story_review(story, config, download_if_missing=True)

    if not res["success"]:
        console.print(f"[bold red]Review preparation failed:[/bold red] {res.get('error')}")
        return False

    questions = res["questions"]
    docx_path = res["docx_path"]
    val = res["validation"]

    console.print(f"[green]✓[/green] Docx loaded: [dim]{docx_path}[/dim]")
    console.print(f"[green]✓[/green] Parsed [bold]{len(questions)} Comprehension Questions[/bold] ({val['summary']})")

    q_table = Table(title=f"Dry-Run Review: Questions & Choices for '{story_name}'", show_header=True, header_style="bold blue")
    q_table.add_column("No.", width=4, justify="center")
    q_table.add_column("Question Rubric", style="white", width=42)
    q_table.add_column("Choices (A, B, C, D)", style="dim", width=50)
    q_table.add_column("Ans", style="bold green", width=5, justify="center")

    for q in questions:
        choices_str = " | ".join(f"{c['letter']}: {c['text'][3:]}" for c in q["choices"])
        q_table.add_row(str(q["num"]), q["raw_question"][:42], choices_str[:50] + "...", q["answer"])

    console.print(q_table)

    # Optional live inspection of current Readora state (Old vs New)
    old_state = None
    if Confirm.ask("[bold cyan]Fetch current Readora questions to view Old vs New comparison?[/bold cyan]", default=True):
        with console.status("[bold yellow]Connecting to Readora to inspect current questions..."):
            readora_res = fetch_story_readora_state(story, config)
        if readora_res.get("success") and "old_state" in readora_res:
            old_state = readora_res["old_state"]
            old_q_list = old_state.get("questions", [])
            console.print(f"[green]✓[/green] Retrieved [bold]{len(old_q_list)} existing questions[/bold] from Readora Lab")

            diff_table = Table(title=f"⚖️ Old (Readora) vs New (Docx) Comparison Matrix: '{story_name}'", show_header=True, header_style="bold magenta")
            diff_table.add_column("#", width=4, justify="center")
            diff_table.add_column("🔴 Readora (Current)", style="dim", width=36)
            diff_table.add_column("Old Ans", style="red", width=8, justify="center")
            diff_table.add_column("🟢 Docx (Incoming)", style="white", width=36)
            diff_table.add_column("New Ans", style="green", width=8, justify="center")
            diff_table.add_column("Diff Action", width=18, justify="center")

            max_len = max(len(old_q_list), len(questions))
            for i in range(max_len):
                oq = old_q_list[i] if i < len(old_q_list) else None
                nq = questions[i] if i < len(questions) else None

                oq_txt = oq.get("question", "")[:35] if oq else "-"
                oq_ans = f"[{oq.get('answer', '')}]" if oq else "[-]"
                nq_txt = nq.get("raw_question", "")[:35] if nq else "-"
                nq_ans = f"[{nq.get('answer', '')}]" if nq else "[-]"

                if oq and nq:
                    if oq.get("answer") != nq.get("answer"):
                        diff_act = f"[red]⚠️ Key: {oq.get('answer')}➔{nq.get('answer')}[/red]"
                    else:
                        diff_act = "[green]✓ Key Match[/green]"
                elif nq:
                    diff_act = "[cyan]✨ New Q[/cyan]"
                else:
                    diff_act = "[yellow]🗑️ Remove[/yellow]"

                diff_table.add_row(str(i+1), oq_txt, oq_ans, nq_txt, nq_ans, diff_act)

            console.print(diff_table)

    header_text = config.get("question_header", "Choose the correct answer ").strip()
    content_type = config.get("content_type", "None")
    old_q_cnt = len(old_state.get("questions", [])) if old_state else "Unknown"

    console.print(Panel(
        f"[bold]Target Changes on Readora Lab:[/bold]\n"
        f"• Current Questions on Server (OLD): [red]{old_q_cnt}[/red]\n"
        f"• Verified Questions to Write (NEW): [green]{len(questions)}[/green]\n"
        f"• Question Header: [cyan]{header_text}[/cyan]\n"
        f"• Content Type: [cyan]{content_type}[/cyan]\n"
        f"• Action: Existing questions will be replaced by verified questions\n"
        f"• Operating Mode: [bold yellow]DRY-RUN REVIEW[/bold yellow]",
        title="Review Summary & Diff",
        border_style="yellow"
    ))

    if Confirm.ask(f"[bold green]Do you want to ACCEPT and APPLY these {len(questions)} questions live to Readora now?[/bold green]", default=False):
        console.print(f"[bold cyan]Connecting to Readora and applying live updates...[/bold cyan]")
        with console.status("[bold green]Writing questions to Readora Lab..."):
            apply_res = execute_accept_and_apply(
                story,
                questions,
                config,
                on_status=lambda msg: console.print(f"  [dim]• {msg}[/dim]")
            )
        if apply_res["success"]:
            console.print(f"\n[bold green]🎉 LIVE SUCCESS: Successfully updated '{story_name}' on Readora Lab![/bold green]")
            console.print(f"  [dim]Matched Title: {apply_res.get('matched_title')}[/dim]")
            console.print(f"  [dim]Edit URL: {apply_res.get('edit_url')}[/dim]\n")
            return True
        else:
            console.print(f"\n[bold red]❌ Failed to apply to Readora:[/bold red] {apply_res.get('error')}\n")
            return False
    else:
        console.print("[bold yellow]Dry-run finished safely. No changes were written to Readora.[/bold yellow]\n")
        return True


def run_automation_flow(stories: List[Dict], config: dict):
    if not stories:
        console.print("[yellow]No stories to process.[/yellow]")
        return

    import time
    mgr = ThreadManager.get_instance()
    worker = StoryAutomationWorker(stories, config, name=f"CLI-Automation-{len(stories)}stories")
    mgr.register_and_start(worker)

    console.print(f"[bold cyan]Automation worker dispatched in background thread [dim]({worker.worker_id})[/dim][/bold cyan]")

    with console.status("[bold green]Executing automation pipeline in background...") as status:
        try:
            while worker.is_running():
                status.update(f"[bold green]{worker.status_message} [dim]({worker.progress:.0f}%)[/dim]")
                time.sleep(0.3)
        except KeyboardInterrupt:
            console.print("\n[yellow]Pausing/Cancelling automation worker...[/yellow]")
            worker.stop()
            worker.join(timeout=5.0)

    if worker.state == WorkerState.COMPLETED:
        console.print(f"[bold green]✓ Automation finished successfully![/bold green]\n")
    elif worker.state == WorkerState.STOPPED:
        console.print(f"[bold yellow]Automation was cancelled by user.[/bold yellow]\n")
    elif worker.state == WorkerState.ERROR:
        console.print(f"[bold red]Automation encountered an error: {worker.last_error}[/bold red]\n")

def list_background_workers():
    mgr = ThreadManager.get_instance()
    summary = mgr.get_summary()
    if not summary:
        console.print("[dim]No background workers registered in ThreadManager.[/dim]\n")
        return

    table = Table(title="Background Workers Status (ThreadManager)", show_header=True, header_style="bold cyan")
    table.add_column("Worker ID", width=10)
    table.add_column("Name", width=25)
    table.add_column("State", width=12)
    table.add_column("Progress", width=10)
    table.add_column("Uptime", width=10)
    table.add_column("Status Message")

    for w in summary:
        color = "green" if w["state"] in ("RUNNING", "COMPLETED") else ("yellow" if w["state"] == "IDLE" else "red")
        table.add_row(
            w["id"],
            w["name"],
            f"[{color}]{w['state']}[/{color}]",
            f"{w['progress']}%",
            f"{w['uptime_seconds']}s",
            w["message"]
        )
    console.print(table)
    console.print()

def interactive_menu():
    config = load_config()
    downloader = DriveDownloader(cache_dir=config.get("cache_dir", "./cache"))
    sheet_parser = SheetParser(
        sheet_url=config.get("sheet_url", ""),
        assigned_to=config.get("assigned_to", "Mohra"),
        cache_dir=config.get("cache_dir", "./cache"),
        sheet_source=config.get("sheet_source", "url")
    )
    progress = ProgressTracker()

    while True:
        display_banner(config.get("dry_run", True))
        console.print("  [bold cyan]1.[/bold cyan] List All Assigned Stories & Status")
        console.print("  [bold cyan]2.[/bold cyan] Process All Pending / Uncompleted Stories (Threaded)")
        console.print("  [bold cyan]3.[/bold cyan] Process a Specific Story by Name (Threaded)")
        console.print("  [bold cyan]4.[/bold cyan] 🔍 Dry-Run Review a Story (Inspect Questions & Accept/Apply)")
        console.print("  [bold cyan]5.[/bold cyan] Test Docx Parsing Only (Local / Drive)")
        console.print("  [bold cyan]6.[/bold cyan] Launch Chrome with Google Profile (`mohrawagdy58@gmail.com`)")
        console.print("  [bold cyan]7.[/bold cyan] Sync Latest Sheet from Google Sheets URL")
        console.print("  [bold cyan]8.[/bold cyan] Toggle Dry-Run Mode")
        console.print("  [bold cyan]9.[/bold cyan] View Settings")
        console.print("  [bold cyan]10.[/bold cyan] View Background Workers Status (ThreadManager)")
        console.print("  [bold cyan]11.[/bold cyan] Exit\n")

        choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"], default="1")

        if choice == "1":
            list_stories(sheet_parser)
        elif choice == "2":
            pending = sheet_parser.get_uncompleted_stories()
            console.print(f"Found [bold yellow]{len(pending)}[/bold yellow] pending stories.")
            if pending:
                if Confirm.ask(f"Do you want to process all {len(pending)} pending stories?", default=True):
                    run_automation_flow(pending, config)
        elif choice == "3":
            query = Prompt.ask("Enter story name (or part of it)")
            story = sheet_parser.find_story(query)
            if story:
                console.print(f"Found: [bold cyan]{story['story_name']}[/bold cyan] ({story['sheet_name']} - Row {story['row_index']})")
                if Confirm.ask("Process this story?", default=True):
                    run_automation_flow([story], config)
            else:
                console.print(f"[red]No story matching '{query}' found.[/red]")
        elif choice == "4":
            query = Prompt.ask("Enter story name to review (or part of it)")
            story = sheet_parser.find_story(query)
            if story:
                review_and_apply_story_cli(story, config)
            else:
                console.print(f"[red]Story '{query}' not found.[/red]")
        elif choice == "5":
            query = Prompt.ask("Enter story name to parse", default="The Friendly Polar Bear")
            story = sheet_parser.find_story(query)
            if story:
                process_single_story(story, config, downloader, progress, client=None)
            else:
                console.print(f"[red]Story '{query}' not found.[/red]")
        elif choice == "6":
            from modules.google_auth import GoogleAuthenticator
            ga = GoogleAuthenticator(config)
            ga.launch_and_login(headless=False)
        elif choice == "7":
            console.print("[yellow]Downloading latest sheet from Google Sheets URL...[/yellow]")
            try:
                dest = sheet_parser.download_sheet(force_refresh=True)
                console.print(f"[green]Successfully synced sheet to: {dest}[/green]\n")
            except Exception as e:
                console.print(f"[red]Failed to sync sheet: {e}[/red]\n")
        elif choice == "8":
            config["dry_run"] = not config.get("dry_run", True)
            save_config(config)
            state = "[bold yellow]ENABLED (Safe)[/bold yellow]" if config["dry_run"] else "[bold red]DISABLED (Live)[/bold red]"
            console.print(f"Dry-run mode is now {state}\n")
        elif choice == "9":
            console.print(Panel(json.dumps(config, indent=2), title="Current Configuration"))
        elif choice == "10":
            list_background_workers()
        elif choice == "11":
            console.print("[dim]Goodbye![/dim]")
            sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description="Mohra App Automation Tool")
    parser.add_argument("--story", type=str, help="Process a specific story by name")
    parser.add_argument("--review", type=str, help="Dry-run review a story and prompt to accept and apply")
    parser.add_argument("--all", action="store_true", help="Process all pending stories")
    parser.add_argument("--list", action="store_true", help="List all assigned stories and exit")
    parser.add_argument("--sync-sheet", action="store_true", help="Force download latest sheet from Google Sheets URL")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode (no live writes)")
    parser.add_argument("--live", action="store_true", help="Force live execution mode (writes to Readora)")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--status", action="store_true", help="Show active background worker threads")

    args = parser.parse_args()
    config = load_config()

    if args.dry_run:
        config["dry_run"] = True
    elif args.live:
        config["dry_run"] = False
    if args.headless:
        config["headless"] = True

    if args.status:
        list_background_workers()
        return

    sheet_parser = SheetParser(
        sheet_url=config.get("sheet_url", ""),
        assigned_to=config.get("assigned_to", "Mohra"),
        cache_dir=config.get("cache_dir", "./cache"),
        sheet_source=config.get("sheet_source", "url")
    )

    if args.sync_sheet:
        console.print("[yellow]Downloading latest sheet from Google Sheets URL...[/yellow]")
        dest = sheet_parser.download_sheet(force_refresh=True)
        console.print(f"[green]Successfully synced sheet to: {dest}[/green]")
        if not (args.list or args.story or args.all):
            return

    if args.list:
        list_stories(sheet_parser)
        return

    if args.review:
        story = sheet_parser.find_story(args.review)
        if not story:
            console.print(f"[red]Story '{args.review}' not found.[/red]")
            sys.exit(1)
        review_and_apply_story_cli(story, config)
        return

    if args.story:
        story = sheet_parser.find_story(args.story)
        if not story:
            console.print(f"[red]Story '{args.story}' not found.[/red]")
            sys.exit(1)
        run_automation_flow([story], config)
        return

    if args.all:
        pending = sheet_parser.get_uncompleted_stories()
        run_automation_flow(pending, config)
        return

    # Default to interactive menu
    interactive_menu()

if __name__ == "__main__":
    main()
